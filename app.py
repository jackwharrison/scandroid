from flask import Flask, render_template, request, send_file, redirect, session, url_for, flash, jsonify, send_from_directory
import requests
from io import BytesIO
from config_loader import load_config, save_config
import json
import os
from config import ADMIN_USERNAME, ADMIN_PASSWORD, FSP_USERNAME, FSP_PASSWORD
from urllib.parse import quote
import subprocess
import zipfile
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A5, landscape
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
import qrcode
import csv
from flask_session import Session
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Register a Unicode-safe font
pdfmetrics.registerFont(TTFont("DejaVu", os.path.join("static", "fonts", "DejaVuSans.ttf")))


app = Flask(__name__)
app.secret_key = 'your_secret_key'
app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_PERMANENT"] = False
Session(app)
def _make_qr_image(data, box_cm=3.0):
    """Return a Pillow image for the QR sized to box_cm × box_cm at 300dpi."""
    qr = qrcode.QRCode(
        version=None, error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10, border=4
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    # resize to cm at 300dpi (ReportLab draws images in points, we’ll scale when drawing)
    target_px = int((box_cm / 2.54) * 300)  # cm -> inches -> px
    img = img.resize((target_px, target_px))
    return img

def _draw_voucher(c, item, static_folder):
    """
    Draw one voucher on an A5 LANDSCAPE page matching the provided layout.
    Supports dynamic CSV fields.
    """
    width, height = landscape(A5)

    margin = 1.0 * cm
    inner_w = width - 2 * margin
    inner_h = height - 2 * margin

    # ---- BORDER -------------------------------------------------------------
    c.setLineWidth(0.8)
    c.rect(margin, margin, inner_w, inner_h)
    logo_height = 2.5 * cm       # height of left logo
    logo_top_y = height - margin - 1.0*cm   # move logos DOWN slightly (was -0.0)

    # LEFT LOGO (ns1)
    try:
        logo1 = ImageReader(os.path.join(static_folder, "ns1.png"))
        c.drawImage(
            logo1,
            margin,                          # <--- as far left as possible
            logo_top_y - logo_height,
            preserveAspectRatio=True,
            height=logo_height,
            mask='auto'
        )
        left_logo_bottom = logo_top_y - logo_height
        left_logo_top = logo_top_y
    except:
        left_logo_top = height - margin - 0.5*cm

    # RIGHT LOGO (ns2) – wide logo, scale by width
    try:
        logo2 = ImageReader(os.path.join(static_folder, "ns2.png"))
        max_width = 5.0 * cm

        img_w, img_h = logo2.getSize()
        scale = max_width / img_w
        scaled_height = img_h * scale

        c.drawImage(
            logo2,
            width - margin - max_width,
            logo_top_y - scaled_height,
            width=max_width,
            height=scaled_height,
            preserveAspectRatio=True,
            mask='auto'
        )
        right_logo_top = logo_top_y
    except:
        right_logo_top = height - margin - 0.5*cm

    # --- CENTER "Project" between logos (vertically aligned to logos) ---
    project_y = min(left_logo_top, right_logo_top) - 0.3*cm

    c.setFont("DejaVu", 16)
    c.drawCentredString(width/2, project_y, "Project")

    # underline "Project"
    text_width = c.stringWidth("Project", "DejaVu", 16)
    c.line(
        (width/2 - text_width/2),
        project_y - 0.08*cm,
        (width/2 + text_width/2),
        project_y - 0.08*cm
    )

    # --- MAIN TITLE just below "Project" ---
    title_y = project_y - 1.4*cm
    c.setFont("DejaVu", 22)
    c.drawCentredString(width/2, title_y, "CASH ON THE MOVE")

    # Subtitle lines
    c.setFont("DejaVu", 11)
    c.drawCentredString(width/2, title_y - 1.0*cm,
                        "Supporting people on the move especially those")
    c.drawCentredString(width/2, title_y - 1.6*cm,
                        "in vulnerable situations")
    # ---- QR CODE ------------------------------------------------------------
    qr_box_size = 5.0 * cm
    qr_x = margin + 0.6*cm
    qr_y = margin + 0.6*cm

    # Outer box
    c.setLineWidth(0.7)
    c.rect(qr_x - 0.1*cm, qr_y - 0.1*cm, qr_box_size + 0.2*cm, qr_box_size + 0.2*cm)

    # QR image
    refid = item.get("referenceid", "").strip()
    qr_img = _make_qr_image(refid, box_cm=3.0)
    c.drawInlineImage(qr_img, qr_x, qr_y, width=qr_box_size, height=qr_box_size)

    # *** Removed “Reference ID below QR” (as you requested) ***

    # ---- BENEFICIARY INFORMATION -------------------------------------------
    info_x = qr_x + qr_box_size + 2.0*cm
    info_y = qr_y + qr_box_size - 0.5*cm
    line_height = 0.75 * cm

    c.setFont("DejaVu", 12)

    # Formatting for dynamic keys
    def pretty_label(raw):
        raw = raw.replace("_", " ")
        raw = ''.join([' ' + ch if ch.isupper() else ch for ch in raw])
        raw = ' '.join(raw.split())
        return raw.title()

    # All dynamic fields except referenceId
    def is_reference_id(key: str) -> bool:
        k = key.strip().lower().replace("_", "").replace(" ", "")
        return ("ref" in k and "id" in k)
        

    # All dynamic fields except the reference ID (robust detection)
    fields_to_print = {
        k: v for k, v in item.items()
        if not is_reference_id(k) and v not in (None, "")
    }


    y = info_y
    for key, value in fields_to_print.items():
        label = pretty_label(key)
        c.setFont("DejaVu", 12)
        c.drawString(info_x, y, f"{label}: {value}")
        y -= line_height

    # ---- SMALL REFERENCE ID AT BOTTOM ------------------------------------
    refid = item.get("referenceid", "").strip()
    if refid:
        c.setFont("DejaVu", 8)
        c.drawString(
            margin + 0.1*cm,
            margin + 0.2*cm,
            f"Reference ID: {refid}"
        )


def generate_vouchers_pdf(rows, static_folder):
    """
    rows: list of dicts with keys: referenceId, name
    returns BytesIO of PDF
    """
    from io import BytesIO
    pdf_io = BytesIO()

    # Create a landscape A5 page
    c = canvas.Canvas(pdf_io, pagesize=landscape(A5))

    for r in rows:
        _draw_voucher(c, r, static_folder)
        c.showPage()

    c.save()
    pdf_io.seek(0)
    return pdf_io

translations = {
"en": {
    "title": "Beneficiary Information",
    "name": "Name",
    "dob": "Date of Birth",
    "photo": "Photo",
    "payment_approved": "Payment Approved",
    "payment_rejected": "Payment Rejected",
    "participant_withdraws": "Participant Withdraws",
    "language": "Language",
    "login": "Login for Red Cross Staff",
    "enter_password": "Enter Password",
    "submit": "Submit",
    "payment_status": "Payment Status",
    "confirm_person": "Confirm this is the correct person",
    "rejection_reason": "Reason for Rejection",
    "already_scanned": "This person has already been scanned and submitted to 121.",
    "success_message": "Payment successfully submitted.",
    "already_submitted_page": "This beneficiary's payment has already been submitted. If you need support, contact support@121.global",
    "config_title": "Configure Fields to Display",
    "field_key": "Field Key (from Kobo)",
    "label_en": "Label (EN)",
    "label_fr": "Label (FR)",
    "label_ar": "Label (AR)",
    "remove": "Remove",
    "add_field": "Add Field",
    "save": "Save",
    "saved_successfully": "Saved successfully",
    "failed_to_save": "Failed to save",
    "logout": "Logout",
    "config_system": "System Configuration",
    "config_display": "Configure Fields to Display",
    "fsp_login": "Login for FSP Admins",
    "fsp_sync_title": "📥 FSP: Sync Offline Records",
    "sync_latest": "Sync Latest Records",
    "syncing": "Syncing...",
    "sync_error": "❌ Failed to sync. Please try again.",
    "sync_initial": "Click sync to see how many beneficiaries are ready for offline validation.",
    "sync_complete": "✅ {count} beneficiaries ready for offline validation.",
    "step1": "Step 1. Sync Latest Records",
    "step2": "Step 2. Import Offline Cache",
    "step3": "Step 3. Scan QR Codes",
    "online": "Online",
    "offline": "Offline",
    "scan_title": "Scan QR",
    "back_to_dashboard": "Back",
    "scan_hint": "Point the camera at the QR code.",
    "start_camera": "Start camera",
    "waiting_to_start": "Waiting to start…",
    "requesting_camera": "Requesting camera… If prompted, tap Allow.",
    "camera_denied": "Camera permission denied or not available.",
    "scanning": "Scanning…",
    "starting_camera": "Starting camera…",
    "footer_dev": "Developed by 510 @ The Netherlands Red Cross",
    "footer_support": "If you need support contact jharrison@redcross.nl",
    "kobo_info": "Kobo Information",
    "kobo_token": "Kobo Token",
    "asset_id": "Kobo Asset ID",
    "fsp_password": "Set Password for FSPs",
    "password": "Password",
    "encryption_settings": "Encryption Settings",
    "encryption_key": "Encryption Key",
    "encryption_warning": "Used to decrypt encrypted fields. If incorrect, offline validation will stop working.",
    "encryption_toggle_warning": "I understand changing this may break the system if incorrect.",
    "info_121": "121 Information",
    "url121": "121 URL",
    "username121": "121 Username",
    "program_id": "121 Program ID",
    "payment_id": "Payment ID",
    "column_to_match": "Field to Match for Payment (e.g., phoneNumber)",
    "column_to_match_info": "This field is selected in the Field Display Config page.",
    "use_for_matching": "Use for Matching Payments",
    "photo_config_title": "Photo Field Configuration",
    "enable_photo_field": "Enable photo field display",
    "home_question": "Who are you?",
    "home_admin": "Red Cross Staff",
    "home_fsp": "Financial Service Provider",
    "fsp_login": "Log in for Financial Service Provider",
    "step_4_generate": "📤 Step 4. Generate Payments to Send to 121",
    "payments_ready": "🔄 Payments ready to submit to 121:",
    "generate_csv": "Generate CSV",
    "download_csv": "⬇️ Download CSV",
    "step_5_send": "✅ Step 5. Send Payments to 121",
    "send_payments": "Send payments",
    "payment_submit_success": "✅ Payments submitted successfully!",
    "payment_submit_failed": "❌ Failed to submit",
    "voucher_generator": "Voucher Generator",
    "csv_hint": "Upload a CSV with referenceId and any extra fields to print",
    "choose_csv": "Choose CSV…",
    "upload_csv": "Upload CSV",
    "download_vouchers": "Download vouchers (PDF)",
    "back": "Back to Dashboard",
    "choose_csv_alert": "Please choose a CSV first.",
    "upload_failed": "Upload failed",
    "voucher_ready_singular": "voucher ready to download",
    "voucher_ready_plural": "vouchers ready to download"        
}
,
"fr": {
    "title": "Informations sur le bénéficiaire",
    "name": "Nom",
    "dob": "Date de naissance",
    "photo": "Photo",
    "payment_approved": "Paiement approuvé",
    "payment_rejected": "Paiement refusé",
    "participant_withdraws": "Le participant se retire",
    "language": "Langue",
    "login": "Connexion pour le personnel de la Croix-Rouge",
    "enter_password": "Entrer le mot de passe",
    "submit": "Soumettre",
    "payment_status": "Statut du paiement",
    "confirm_person": "Confirmez que c'est la bonne personne",
    "rejection_reason": "Motif du refus",
    "already_scanned": "Cette personne a déjà été scannée et soumise à 121.",
    "success_message": "Paiement soumis avec succès.",
    "already_submitted_page": "Le paiement de ce bénéficiaire a déjà été soumis. Si vous avez besoin d'aide, contactez support@121.global",
    "config_title": "Configurer les champs à afficher",
    "field_key": "Clé de champ (depuis Kobo)",
    "label_en": "Libellé (EN)",
    "label_fr": "Libellé (FR)",
    "label_ar": "Libellé (AR)",
    "remove": "Supprimer",
    "add_field": "Ajouter un champ",
    "save": "Enregistrer",
    "saved_successfully": "Enregistré avec succès",
    "failed_to_save": "Échec de l'enregistrement",
    "logout": "Déconnexion",
    "config_system": "Configuration du système",
    "config_display": "Configurer les champs à afficher",
    "fsp_login": "Connexion pour les FSP",
    "fsp_sync_title": "📥 FSP : Synchroniser les enregistrements hors ligne",
    "sync_latest": "Synchroniser les derniers enregistrements",
    "syncing": "Synchronisation...",
    "sync_error": "❌ Échec de la synchronisation. Veuillez réessayer.",
    "sync_initial": "Cliquez sur synchroniser pour voir combien de bénéficiaires sont prêts pour la validation hors ligne.",
    "sync_complete": "✅ {count} bénéficiaires prêts pour la validation hors ligne.",
    "step1": "Étape 1. Synchroniser les derniers enregistrements",
    "step2": "Étape 2. Importer le cache hors ligne",
    "step3": "Étape 3. Scanner les codes QR",
    "online": "En ligne",
    "offline": "Hors ligne",
    "scan_title": "Scanner un QR",
    "back_to_dashboard": "Retour au tableau de bord",
    "scan_hint": "Pointez la caméra vers le code QR.",
    "start_camera": "Démarrer la caméra",
    "waiting_to_start": "En attente de démarrage…",
    "requesting_camera": "Demande d’accès à la caméra… Si demandé, touchez Autoriser.",
    "camera_denied": "Accès à la caméra refusé ou non disponible.",
    "scanning": "Analyse…",
    "starting_camera": "Démarrage de la caméra…",
    "footer_dev": "Développé par 510 @ La Croix-Rouge néerlandaise",
    "footer_support": "Pour toute assistance, contactez jharrison@redcross.nl",
    "kobo_info": "Informations Kobo",
    "kobo_token": "Jeton Kobo",
    "asset_id": "ID d'actif Kobo",
    "fsp_password": "Définir un mot de passe pour les FSP",
    "password": "Mot de passe",
    "encryption_settings": "Paramètres de chiffrement",
    "encryption_key": "Clé de chiffrement",
    "encryption_warning": "Utilisée pour déchiffrer les champs. Si elle est incorrecte, la validation hors ligne ne fonctionnera pas.",
    "encryption_toggle_warning": "Je comprends que changer cela pourrait casser le système si incorrect.",
    "info_121": "Informations 121",
    "url121": "URL 121",
    "username121": "Nom d'utilisateur 121",
    "program_id": "ID du programme 121",
    "payment_id": "ID de paiement",
    "column_to_match": "Champ à faire correspondre pour le paiement (ex. : phoneNumber)",
    "column_to_match_info": "Ce champ est sélectionné dans la page de configuration d'affichage.",
    "use_for_matching": "Utiliser pour le rapprochement des paiements",
    "photo_config_title": "Configuration du champ photo",
    "enable_photo_field": "Activer l'affichage du champ photo",
    "home_question": "Qui es-tu?",
    "home_admin": "Personnel de la Croix-Rouge",
    "home_fsp": "Prestataire de services financiers",
    "fsp_login": "Connexion pour le prestataire de services financiers",
    "step_4_generate": "📤 Étape 4. Générer les paiements à envoyer à 121",
    "payments_ready": "🔄 Paiements prêts à être soumis à 121 :",
    "generate_csv": "Générer un CSV",
    "download_csv": "⬇️ Télécharger le CSV",
    "step_5_send": "✅ Étape 5. Envoyer les paiements à 121",
    "send_payments": "Envoyer les paiements",
    "payment_submit_success": "✅ Paiements envoyés avec succès !",
    "payment_submit_failed": "❌ Échec de l'envoi",
    "voucher_generator": "Générateur de bons",
    "csv_hint": "Téléversez un CSV avec referenceId et d’autres champs à imprimer",
    "choose_csv": "Choisir un CSV…",
    "upload_csv": "Téléverser un CSV",
    "download_vouchers": "Télécharger les bons (PDF)",
    "back": "Retour au tableau de bord",
    "choose_csv_alert": "Veuillez d'abord choisir un fichier CSV.",
    "upload_failed": "Échec du téléversement",
    "voucher_ready_singular": "bon prêt à télécharger",
    "voucher_ready_plural": "bons prêts à télécharger"
}
,
"ar": {
    "title": "معلومات المستفيد",
    "name": "الاسم",
    "dob": "تاريخ الميلاد",
    "photo": "صورة",
    "payment_approved": "تمت الموافقة على الدفع",
    "payment_rejected": "تم رفض الدفع",
    "participant_withdraws": "انسحب المستفيد",
    "language": "اللغة",
    "login": "تسجيل دخول لموظفي الهلال الأحمر",
    "enter_password": "أدخل كلمة المرور",
    "submit": "إرسال",
    "payment_status": "حالة الدفع",
    "confirm_person": "تأكيد أن هذه هي الشخص الصحيح",
    "rejection_reason": "سبب الرفض",
    "already_scanned": "تم بالفعل مسح هذا الشخص وإرساله إلى 121.",
    "success_message": "تم إرسال الدفع بنجاح.",
    "already_submitted_page": "تم بالفعل إرسال دفعة هذا المستفيد. إذا كنت بحاجة إلى الدعم، فاتصل بـ support@121.global",
    "config_title": "تكوين الحقول المعروضة",
    "field_key": "مفتاح الحقل (من كوبا)",
    "label_en": "التسمية (EN)",
    "label_fr": "التسمية (FR)",
    "label_ar": "التسمية (AR)",
    "remove": "إزالة",
    "add_field": "إضافة حقل",
    "save": "حفظ",
    "saved_successfully": "تم الحفظ بنجاح",
    "failed_to_save": "فشل الحفظ",
    "logout": "تسجيل الخروج",
    "config_system": "إعدادات النظام",
    "config_display": "تكوين الحقول المعروضة",
    "fsp_login": "تسجيل الدخول لمزودي الخدمات المالية",
    "fsp_sync_title": "📥 مزوّد الخدمة: مزامنة السجلات غير المتصلة",
    "sync_latest": "مزامنة أحدث السجلات",
    "syncing": "جارٍ المزامنة...",
    "sync_error": "❌ فشلت عملية المزامنة. يرجى المحاولة مرة أخرى.",
    "sync_initial": "انقر على المزامنة لمعرفة عدد المستفيدين الجاهزين للتحقق دون اتصال.",
    "sync_complete": "✅ {count} مستفيدين جاهزين للتحقق دون اتصال.",
    "step1": "الخطوة 1. مزامنة أحدث السجلات",
    "step2": "الخطوة 2. استيراد ذاكرة التخزين المؤقت دون اتصال",
    "step3": "الخطوة 3. مسح رموز QR",
    "online": "متصل",
    "offline": "غير متصل",
    "scan_title": "مسح رمز QR",
    "back_to_dashboard": "العودة إلى لوحة التحكم",
    "scan_hint": "وجّه الكاميرا نحو رمز QR.",
    "start_camera": "بدء تشغيل الكاميرا",
    "waiting_to_start": "بانتظار البدء…",
    "requesting_camera": "جارٍ طلب تشغيل الكاميرا… إذا طُلِب منك ذلك، اضغط سماح.",
    "camera_denied": "تم رفض إذن الكاميرا أو أنها غير متاحة.",
    "scanning": "جارٍ المسح…",
    "starting_camera": "جارٍ بدء تشغيل الكاميرا…",
    "footer_dev": "تم التطوير بواسطة 510 @ الصليب الأحمر الهولندي",
    "footer_support": "إذا كنت بحاجة إلى الدعم، تواصل مع jharrison@redcross.nl",
    "kobo_info": "معلومات كوبا",
    "kobo_token": "رمز كوبا",
    "asset_id": "معرف الأصول في كوبا",
    "fsp_password": "تعيين كلمة مرور لـ FSP",
    "password": "كلمة المرور",
    "encryption_settings": "إعدادات التشفير",
    "encryption_key": "مفتاح التشفير",
    "encryption_warning": "يُستخدم لفك تشفير الحقول المشفرة. إذا كان غير صحيح، فلن تعمل التحقق بدون اتصال.",
    "encryption_toggle_warning": "أفهم أن التغيير هنا قد يؤدي إلى تعطل النظام إذا كان غير صحيح.",
    "info_121": "معلومات 121",
    "url121": "رابط 121",
    "username121": "اسم مستخدم 121",
    "program_id": "معرف برنامج 121",
    "payment_id": "معرف الدفع",
    "column_to_match": "الحقل المطابق للدفع (مثل رقم الهاتف)",
    "column_to_match_info": "يتم تحديد هذا الحقل في صفحة إعداد عرض الحقول.",
    "use_for_matching": "استخدم لمطابقة المدفوعات",
    "photo_config_title": "إعدادات حقل الصورة",
    "enable_photo_field": "تفعيل عرض حقل الصورة",
    "home_question": "من أنت؟",
    "home_admin": "موظفو الصليب الأحمر",
    "home_fsp": "مزود خدمات مالية",
    "fsp_login": "تسجيل الدخول لمزود الخدمة المالية",
    "step_4_generate": "📤 الخطوة 4. إنشاء الدفعات لإرسالها إلى 121",
    "payments_ready": "🔄 الدفعات الجاهزة للإرسال إلى 121:",
    "generate_csv": "إنشاء ملف CSV",
    "download_csv": "⬇️ تحميل ملف CSV",
    "step_5_send": "✅ الخطوة 5. إرسال الدفعات إلى 121",
    "send_payments": "إرسال الدفعات",
    "payment_submit_success": "✅ تم إرسال المدفوعات بنجاح!",
    "payment_submit_failed": "❌ فشل الإرسال",
    "voucher_generator": "مولّد القسائم",
    "csv_hint": "قم بتحميل ملف CSV يحتوي على referenceId وأي حقول إضافية للطباعة",
    "choose_csv": "اختر ملف CSV…",
    "upload_csv": "تحميل ملف CSV",
    "download_vouchers": "تنزيل القسائم (PDF)",
    "back": "العودة إلى لوحة التحكم",
    "choose_csv_alert": "يرجى اختيار ملف CSV أولاً.",
    "upload_failed": "فشل الرفع",
    "voucher_ready_singular": "قسيمة جاهزة للتنزيل",
    "voucher_ready_plural": "قسائم جاهزة للتنزيل"
    }
}


@app.route("/")
def landing_page():
    lang = request.args.get("lang", "en")
    return render_template("home.html", lang=lang, t=translations.get(lang, translations["en"]))


@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    from config import ADMIN_USERNAME, ADMIN_PASSWORD
    lang = request.args.get("lang", "en")

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return redirect(url_for("admin_dashboard", lang=lang))
        else:
            flash("Invalid credentials", "error")

    return render_template("admin_login.html", lang=lang, t=translations.get(lang, translations["en"]))

@app.route("/admin-dashboard")
def admin_dashboard():
    lang = request.args.get("lang", "en")
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login", lang=lang))
    
    return render_template("admin_dashboard.html", lang=lang, t=translations.get(lang, translations["en"]))

@app.route("/admin-logout")
def admin_logout():
    lang = request.args.get("lang", "en")
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin_login", lang=lang))


@app.route("/system-config", methods=["GET", "POST"])
def system_config():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login", lang=request.args.get("lang", "en")))

    lang = request.args.get("lang", "en")
    t = translations.get(lang, translations["en"])

    if request.method == "POST":
        updated_config = {
            "KOBO_TOKEN": request.form.get("KOBO_TOKEN", ""),
            "ASSET_ID": request.form.get("ASSET_ID", ""),
            "PASSWORD": request.form.get("PASSWORD", ""),
            "url121": request.form.get("url121", ""),
            "username121": request.form.get("username121", ""),
            "password121": request.form.get("password121", ""),
            "programId": request.form.get("programId", ""),
            "PAYMENT_ID": request.form.get("PAYMENT_ID", ""),
            "COLUMN_TO_MATCH": request.form.get("COLUMN_TO_MATCH", ""),            
            "ENCRYPTION_KEY": request.form.get("ENCRYPTION_KEY", "")
        }
        save_config(updated_config)
        flash(t["saved_successfully"])
        return redirect(url_for("system_config", lang=lang))

    config = load_config()
    return render_template("system_config.html", config=config, lang=lang, t=t)


@app.route("/config", methods=["GET", "POST"])
def config_page():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login", lang=request.args.get("lang", "en")))

    lang = request.args.get("lang", "en")

    if request.method == "POST":
        config_data = request.get_json()
        try:
            with open("display_config.json", "w", encoding="utf-8") as f:
                json.dump(config_data, f, ensure_ascii=False, indent=2)
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    # Handle GET request
    try:
        with open("display_config.json", "r", encoding="utf-8") as f:
            config_data = json.load(f)

        if isinstance(config_data, list):
            config_data = {
                "fields": config_data,
                "photo": {
                    "enabled": True,
                    "labels": {
                        "en": "Photo",
                        "fr": "Photo",
                        "ar": "صورة"
                    }
                }
            }
    except Exception:
        config_data = {
            "fields": [],
            "photo": {
                "enabled": True,
                "labels": {
                    "en": "Photo",
                    "fr": "Photo",
                    "ar": "صورة"
                }
            }
        }

    # 🧠 This is the missing part:
    try:
        with open("system_config.json", "r", encoding="utf-8") as f:
            system_config = json.load(f)
    except Exception:
        system_config = {}

    return render_template(
        "config.html",
        config=config_data,
        system_config=system_config,  # ← You need to pass this!
        lang=lang,
        t=translations.get(lang, translations["en"])
    )

def get_121_token():
    config = load_config()
    payload = {"username": config["username121"], "password": config["password121"]}
    response = requests.post(f"{config['url121']}/api/users/login", json=payload)
    if response.status_code == 201:
        return response.json().get("access_token_general")
    return None

def get_registration_data(reference_id, token):
    config = load_config()
    headers = {'Content-Type': 'application/json'}
    cookies = {'access_token_general': token}
    params = {'referenceId': reference_id}
    url = f"{config['url121']}/api/programs/{config['programId']}/registrations/"
    response = requests.get(url, headers=headers, cookies=cookies, params=params)
    if response.status_code == 200:
        for entry in response.json().get("data", []):
            if entry.get("referenceId") == reference_id:
                return entry
    return None

@app.route("/")
def home():
    return redirect("/login")


@app.route("/logout")
def logout():
    lang = request.args.get("lang", "en")
    session.clear()
    return redirect(url_for("login", lang=lang))


@app.route("/update_status", methods=["POST"])
def update_status():
    import csv
    import io
    from datetime import datetime
    import os

    config = load_config()
    ben_id = request.form.get("id")
    status = request.form.get("status")
    rejection_reason = request.form.get("rejection_reason", "")
    lang = request.form.get("lang", "en")

    column_to_match = config.get("COLUMN_TO_MATCH", "phoneNumber")
    payment_id = config.get("PAYMENT_ID")
    program_id = config.get("programId")

    if not payment_id or not program_id:
        flash("Missing Payment ID or Program ID in system config", "error")
        return redirect(f"/beneficiary?id={ben_id}&lang={lang}")

    # Step 1: Get Kobo UUID
    headers = {"Authorization": f"Token {config['KOBO_TOKEN']}"}
    kobo_resp = requests.get(
        f"https://kobo.ifrc.org/api/v2/assets/{config['ASSET_ID']}/data/{ben_id}/?format=json", 
        headers=headers
    )
    if kobo_resp.status_code != 200:
        flash("Could not retrieve data from Kobo", "error")
        return redirect(f"/beneficiary?id={ben_id}&lang={lang}")

    kobo_data = kobo_resp.json()
    uuid = kobo_data.get("_uuid")
    if not uuid:
        flash("UUID not found in Kobo record", "error")
        return redirect(f"/beneficiary?id={ben_id}&lang={lang}")

    # Step 2: Get payment transactions
    token = get_121_token()
    if not token:
        flash("Login to 121 failed", "error")
        return redirect(f"/beneficiary?id={ben_id}&lang={lang}")

    tx_url = f"{config['url121']}/api/programs/{program_id}/payments/{payment_id}/transactions"
    tx_resp = requests.get(tx_url, cookies={"access_token_general": token})
    if tx_resp.status_code != 200:
        flash(f"Failed to fetch transactions: {tx_resp.text}", "error")
        return redirect(f"/beneficiary?id={ben_id}&lang={lang}")

    transactions = tx_resp.json()
    tx = next((t for t in transactions if t.get("registrationReferenceId") == uuid), None)
    if not tx:
        flash("No transaction found for this beneficiary in the payment", "error")
        return redirect(f"/beneficiary?id={ben_id}&lang={lang}")

    registration_id = tx.get("registrationId")
    if not registration_id:
        flash("No registrationId found in matching transaction", "error")
        return redirect(f"/beneficiary?id={ben_id}&lang={lang}")

    # Step 3: Get full registration using ID (numeric)
    reg_url = f"{config['url121']}/api/programs/{program_id}/registrations/{registration_id}"
    reg_resp = requests.get(reg_url, cookies={"access_token_general": token})
    if reg_resp.status_code != 200:
        flash(f"Could not fetch registration details: {reg_resp.text}", "error")
        return redirect(f"/beneficiary?id={ben_id}&lang={lang}")

    registration = reg_resp.json()
    match_value = registration.get(column_to_match)
    if not match_value:
        flash(f"Field '{column_to_match}' not found in registration", "error")
        return redirect(f"/beneficiary?id={ben_id}&lang={lang}")

    # Step 4: Build reconciliation CSV
    status_value = "success" if status == "Payment Approved" else "error"
    csv_buffer = io.StringIO()
    writer = csv.DictWriter(csv_buffer, fieldnames=[column_to_match, "status"])
    writer.writeheader()
    writer.writerow({column_to_match: match_value, "status": status_value})

    # Step 5: Upload to reconciliation endpoint
    upload_url = f"{config['url121']}/api/programs/{program_id}/payments/{payment_id}/excel-reconciliation"
    files = {"file": ("reconciliation.csv", csv_buffer.getvalue(), "text/csv")}
    upload_resp = requests.post(upload_url, files=files, cookies={"access_token_general": token})

    # Step 6: Log result
    log_row = {
        "timestamp": datetime.utcnow().isoformat(),
        "beneficiary_id": ben_id,
        "uuid": uuid,
        "match_column": column_to_match,
        "match_value": match_value,
        "status": status,
        "rejection_reason": rejection_reason,
        "success": upload_resp.status_code == 201
    }

    log_exists = os.path.exists("reconciliation_log.csv")
    with open("reconciliation_log.csv", "a", newline='', encoding="utf-8") as log_file:
        writer = csv.DictWriter(log_file, fieldnames=log_row.keys())
        if not log_exists:
            writer.writeheader()
        writer.writerow(log_row)

    if upload_resp.status_code == 201:
        return redirect(f"/success?lang={lang}")
    else:
        flash(f"Reconciliation upload failed: {upload_resp.text}", "error")
        return redirect(f"/beneficiary?id={ben_id}&lang={lang}")

@app.route("/fsp-login", methods=["GET", "POST"])
def fsp_login():
    from config import FSP_USERNAME, FSP_PASSWORD
    lang = request.args.get("lang", "en")

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        if username == FSP_USERNAME and password == FSP_PASSWORD:
            session["fsp_logged_in"] = True
            return redirect(url_for("fsp_admin", lang=lang))
        else:
            flash("Invalid credentials", "error")

    return render_template("fsp_login.html", lang=lang, t=translations.get(lang, translations["en"]))



@app.route("/fsp-admin")
def fsp_admin():
    if not session.get("fsp_logged_in"):
        return redirect(url_for("fsp_login"))

    lang = request.args.get("lang", "en")
    t = translations.get(lang, translations["en"])
    return render_template("fsp_admin.html", lang=lang, t=t)


@app.route("/sync-fsp")
def sync_fsp():
    import subprocess

    try:
        result = subprocess.run(
            ["python", "offline_sync.py"],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors="replace"
        )
        output = result.stdout.strip()
        error_output = result.stderr.strip()

        print("\n[DEBUG] STDOUT:\n", output)
        print("\n[DEBUG] STDERR:\n", error_output)

        if result.returncode != 0:
            return jsonify({
                "success": False,
                "message": f"❌ Script failed with error:\n{error_output or output}"
            })

        for line in output.splitlines():
            if "beneficiaries" in line.lower():
                return jsonify({"success": True, "message": f"✅ {line.strip()}"})

        return jsonify({
            "success": True,
            "message": "✅ Sync completed, but no beneficiaries were found."
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"❌ Error running sync: {str(e)}"
        })
@app.route("/fsp-logout")
def fsp_logout():
    session.pop("fsp_logged_in", None)
    return redirect(url_for("fsp_login"))


@app.route("/scan")
def scan():
    # Only FSP-logged-in users should scan
    lang = request.args.get("lang", "en")
    if not session.get("fsp_logged_in"):
        return redirect(url_for("fsp_login", lang=lang))
    return render_template("scan.html", lang=lang, t=translations.get(lang, translations["en"]))


@app.route('/service-worker.js')
def sw():
    return send_from_directory('static', 'service-worker.js', mimetype='application/javascript')

@app.route('/manifest.webmanifest')
def manifest():
    return send_from_directory('static', 'manifest.webmanifest', mimetype='application/manifest+json')

@app.route('/offline')
def offline():
    return render_template('offline.html')


from io import BytesIO

@app.route("/api/offline/latest.zip")
def api_offline_latest_zip():
    base_dir = "offline-cache"
    if not os.path.isdir(base_dir):
        return jsonify({"error": "No offline cache found"}), 404

    # Find latest batch directory by modified time
    batch_dirs = [
        os.path.join(base_dir, d)
        for d in os.listdir(base_dir)
        if os.path.isdir(os.path.join(base_dir, d))
    ]
    if not batch_dirs:
        return jsonify({"error": "No batches found"}), 404

    latest = max(batch_dirs, key=os.path.getmtime)

    # Zip the latest batch in memory
    mem = BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(latest):
            for fname in files:
                full_path = os.path.join(root, fname)
                arcname = os.path.relpath(full_path, latest)  # keep paths relative to batch root
                zf.write(full_path, arcname)
    mem.seek(0)

    return send_file(
        mem,
        mimetype="application/zip",
        as_attachment=True,
        download_name="latest_offline_cache.zip",
    )

@app.route("/ping")
def ping():
    return ("", 204)


@app.route("/beneficiary-offline")
def beneficiary_offline():
    # expected: /beneficiary-offline?uuid=<registrationReferenceId>&lang=en
    uuid = request.args.get("uuid")
    lang = request.args.get("lang", "en")

    # CHANGE: don't return 400; render a shell so the SW can precache a 200
    if not uuid:
        uuid = ""

    # load display config (same file you already use)
    try:
        with open("display_config.json", "r", encoding="utf-8") as f:
            full_config = json.load(f)
            display_fields = full_config.get("fields", [])
            photo_config = full_config.get("photo", {})
    except Exception:
        display_fields = []
        photo_config = {}

    # pass Fernet key for client-side decrypt when we add it
    config = load_config()
    enc_key = config.get("ENCRYPTION_KEY", "")
    column_to_match = config.get("COLUMN_TO_MATCH", "phoneNumber")

    return render_template(
        "beneficiary_offline.html",
        uuid=uuid,  # may be "", the page will prefer URL ?uuid=...
        lang=lang,
        t=translations.get(lang, translations["en"]),
        display_fields=display_fields,
        photo_config=photo_config,
        fernet_key=enc_key,
        column_to_match=column_to_match
    )

@app.route("/success-offline")
def success_offline():
    lang = request.args.get("lang", "en")
    return render_template("success_offline.html", lang=lang)

@app.route("/system-config.json")
def system_config_json():
    config = load_config()
    return jsonify({
        "COLUMN_TO_MATCH": config.get("COLUMN_TO_MATCH", "phoneNumber")
    })




@app.route('/submit-payments', methods=['POST'])
def submit_payments():
    import csv
    import io
    import os
    import json
    from datetime import datetime
    from cryptography.fernet import Fernet

    config = load_config()
    program_id = config.get("programId")
    fernet_key = config.get("ENCRYPTION_KEY")

    if not program_id:
        return "❌ Missing programId in system_config.json", 400

    if not fernet_key:
        return "❌ Missing ENCRYPTION_KEY in system_config.json", 400

    # Set up decryption
    try:
        fernet = Fernet(fernet_key.encode())
    except Exception as e:
        return f"❌ Invalid Fernet key: {e}", 400

    # Get uploaded file
    if 'csv' not in request.files:
        return "❌ No CSV file provided", 400

    file = request.files['csv']
    if file.filename == '':
        return "❌ Empty filename", 400

    try:
        csv_content = file.stream.read().decode("utf-8")
    except Exception as e:
        return f"❌ Failed to read CSV: {e}", 400

    reader = csv.DictReader(io.StringIO(csv_content))
    rows = list(reader)

    if not rows:
        return "❌ CSV is empty", 400

    # --- Locate latest cache and read batch_info.json ---
    # --- Load latest batch and build phone -> paymentId map ---
    cache_base = "offline-cache"

    import re

    def extract_batch_number(name):
        match = re.search(r"payment-recent-batch-(\d+)", name)
        return int(match.group(1)) if match else -1

    batch_dirs = sorted(
        [d for d in os.listdir(cache_base) if d.startswith("payment-recent-batch-")],
        key=extract_batch_number
    )

    if not batch_dirs:
        return "❌ No recent payment batches found — run sync first.", 400

    latest_batch = batch_dirs[-1]
    print(f"[DEBUG] Using batch folder: {latest_batch}")

    reg_cache_path = os.path.join(cache_base, latest_batch, "registrations_cache.json")
    if not os.path.exists(reg_cache_path):
        return "❌ registrations_cache.json missing — run sync again.", 400

    try:
        with open(reg_cache_path, "r", encoding="utf-8") as f:
            reg_data = json.load(f)
    except Exception as e:
        return f"❌ Failed to load registrations_cache.json — {e}", 500

    phone_to_pid = {}
    for record in reg_data:
        uuid = record.get("uuid")
        payment_id = record.get("paymentId")  # Make sure this exists in each record during sync!
        encrypted_phone = record.get("data", {}).get("phoneNumber", "")
        if encrypted_phone and payment_id:
            try:
                decrypted_phone = fernet.decrypt(encrypted_phone.encode()).decode().strip()
                phone_to_pid[decrypted_phone] = payment_id
            except Exception as e:
                print(f"[!] Failed to decrypt phone for UUID {uuid}: {e}")

    # --- Group rows by paymentId ---
    grouped = {}
    for row in rows:
        phone = row.get('phoneNumber', '').strip()
        status = row.get('status', '').strip()

        if phone.startswith("gAAAA"):
            try:
                phone = fernet.decrypt(phone.encode()).decode().strip()
            except Exception as e:
                print(f"[!] Failed to decrypt incoming phoneNumber: {phone} — {e}")
                continue

        payment_id = phone_to_pid.get(phone)
        if not payment_id:
            print(f"[!] No paymentId found for phoneNumber: {phone}")
            continue

        grouped.setdefault(payment_id, []).append({"phoneNumber": phone, "status": status})

    if not grouped:
        return "❌ No valid rows to submit — check your CSV and sync data.", 400

    # --- Submit to 121 by paymentId ---
    token = get_121_token()
    if not token:
        return "❌ Login to 121 failed", 401

    success_count = 0
    fail_count = 0

    for pid, items in grouped.items():
        output_buffer = io.StringIO()
        writer = csv.DictWriter(output_buffer, fieldnames=["phoneNumber", "status"])
        writer.writeheader()
        for item in items:
            writer.writerow(item)

        upload_url = f"{config['url121']}/api/programs/{program_id}/payments/{pid}/excel-reconciliation"
        files = {"file": ("reconciliation.csv", output_buffer.getvalue(), "text/csv")}
        upload_resp = requests.post(upload_url, files=files, cookies={"access_token_general": token})

        if upload_resp.status_code == 201:
            success_count += 1
            print(f"[OK] Submitted to paymentId {pid}")
        else:
            fail_count += 1
            print(f"[ERROR] Failed to submit to paymentId {pid}: {upload_resp.status_code} — {upload_resp.text}")

    if success_count > 0:
        return f"✅ Submitted to {success_count} paymentId(s). ❌ {fail_count} failed.", 200
    else:
        return "❌ All submissions failed.", 500


    upload_url = f"{config['url121']}/api/programs/{program_id}/payments/{payment_id}/excel-reconciliation"
    files = {"file": ("reconciliation.csv", output_buffer.getvalue(), "text/csv")}
    upload_resp = requests.post(upload_url, files=files, cookies={"access_token_general": token})

    # Log result
    log_path = 'bulk_submit_log.csv'
    log_exists = os.path.exists(log_path)
    with open(log_path, "a", newline='', encoding="utf-8") as log_file:
        log_writer = csv.DictWriter(log_file, fieldnames=["timestamp", "phoneNumber", "status", "success"])
        if not log_exists:
            log_writer.writeheader()
        for row in rows:
            log_writer.writerow({
                "timestamp": datetime.utcnow().isoformat(),
                "phoneNumber": row.get('phoneNumber'),
                "status": row.get('status'),
                "success": upload_resp.status_code == 201
            })

    if upload_resp.status_code == 201:
        return "✅ Submission successful", 201
    else:
        print(f"[ERROR] Upload to 121 failed: {upload_resp.status_code} — {upload_resp.text}")
        return f"❌ Submission failed: {upload_resp.text}", upload_resp.status_code

@app.route('/invalid-qr')
def invalid_qr():
    reason = request.args.get('reason', 'Invalid QR code')
    lang = request.args.get('lang', 'en')
    return render_template('invalid-qr.html', reason=reason, lang=lang)

@app.route("/vouchers", methods=["GET"])
def vouchers_page():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login", lang=request.args.get("lang", "en")))

    lang = request.args.get("lang", "en")
    t = translations.get(lang, translations["en"])   # ✅ FIX

    return render_template("vouchers.html", lang=lang, t=t)


@app.route("/vouchers/upload", methods=["POST"])
def vouchers_upload():
    if not session.get("admin_logged_in"):
        return jsonify({"success": False, "message": "Not authorized"}), 403

    if "csv" not in request.files:
        return jsonify({"success": False, "message": "No CSV uploaded"}), 400

    f = request.files["csv"]

    try:
        # Ensure upload directory exists
        import os
        os.makedirs("uploads", exist_ok=True)

        # Save raw CSV file to server (not session)
        upload_path = os.path.join("uploads", "vouchers.csv")
        f.save(upload_path)

        # Re-open and parse (same as before)
        with open(upload_path, "r", encoding="utf-8", errors="replace") as infile:
            content = infile.read()

        reader = csv.DictReader(content.splitlines())
        rows = []

        for row in reader:
            clean_row = {}

            # --- Normalize all header names ---
            for key, value in row.items():
                if key is None:
                    continue
                clean_key = key.strip().replace("\ufeff", "").lower()
                clean_row[clean_key] = value.strip() if isinstance(value, str) else value

            # --- Ensure 'referenceid' key exists ---
            ref = (
                clean_row.get("referenceid")
                or clean_row.get("reference id")
                or clean_row.get("reference_id")
                or clean_row.get("refid")
                or clean_row.get("id")
                or clean_row.get("registrationReferenceId")
                or ""
            )

            clean_row["referenceid"] = ref.strip()

            rows.append(clean_row)

        # ✅ Store ONLY the file path + row count in the session (small!)
        session["voucher_file_path"] = upload_path
        session["voucher_count"] = len(rows)

        # ✅ Do NOT store rows in session — they can be reloaded from file whenever needed

        return jsonify({"success": True, "count": len(rows)})

    except Exception as e:
        return jsonify({"success": False, "message": f"Failed to parse CSV: {e}"}), 400

@app.route("/vouchers/download", methods=["GET"])
def vouchers_download():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login", lang=request.args.get("lang", "en")))

    # Load file path saved during upload
    csv_path = session.get("voucher_file_path")
    if not csv_path or not os.path.exists(csv_path):
        flash("No uploaded data to generate vouchers.", "error")
        return redirect(url_for("vouchers_page"))

    # Read and reconstruct voucher rows
    rows = []
    with open(csv_path, "r", encoding="utf-8", errors="replace") as infile:
        reader = csv.DictReader(infile.read().splitlines())

        for row in reader:
            clean_row = {}

            # Clean & normalize header keys
            for key, value in row.items():
                if key is None:
                    continue
                clean_key = key.strip().replace("\ufeff", "").lower()
                clean_row[clean_key] = value.strip() if isinstance(value, str) else value

            # Ensure referenceid always exists
            ref = (
                clean_row.get("referenceid")
                or clean_row.get("reference id")
                or clean_row.get("reference_id")
                or clean_row.get("refid")
                or clean_row.get("id")
                or ""
            )
            clean_row["referenceid"] = ref.strip()

            rows.append(clean_row)

    # Now generate the PDF from reconstructed rows
    pdf_io = generate_vouchers_pdf(
        rows,
        static_folder=os.path.join(app.root_path, "static")
    )

    return send_file(
        pdf_io,
        mimetype="application/pdf",
        as_attachment=True,
        download_name="vouchers.pdf"
    )
