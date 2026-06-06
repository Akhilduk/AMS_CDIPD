import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
STORAGE_DIR = BASE_DIR / "storage"
QR_DIR = STORAGE_DIR / "qr"
PDF_DIR = STORAGE_DIR / "pdf"
POLICY_DIR = STORAGE_DIR / "policy"

DB_PATH = DATA_DIR / "asset_management.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")
SECRET_KEY = os.getenv("SECRET_KEY", "local-demo-secret")
AUTH_ADAPTER = os.getenv("AUTH_ADAPTER", "local").lower()
HRMS_SSO_URL = os.getenv("HRMS_SSO_URL", "")
DEMO_OTP_MODE = os.getenv("DEMO_OTP_MODE", "true").lower() == "true"
POLICY_SAMPLE_FILE = POLICY_DIR / "CDIPD_HR_Asset_Usage_Policy_Ver_1_0.pdf"

FLASH_COOKIE = "flash_message"
OTP_RESEND_COOLDOWN_SECONDS = 60

for directory in [DATA_DIR, STORAGE_DIR, QR_DIR, PDF_DIR, POLICY_DIR]:
    directory.mkdir(parents=True, exist_ok=True)

POLICY_SECTIONS = [
    {
        "title": "1. Purpose",
        "paragraphs": [
            "This policy describes the controls necessary to minimise information security risks and prevent damage to IT assets, including laptops, desktops, mobile devices, and any other assets issued to CDIPD employees."
        ],
    },
    {
        "title": "2. Applicability",
        "paragraphs": [
            "This policy applies to all employees, including those on contract, trainees, employees on probation, and consultants, who hold roles within CDIPD."
        ],
    },
    {
        "title": "3. Definition of IT Assets",
        "bullets": [
            "Laptops, desktops, tablets",
            "Mobile phones, data cards",
            "Monitors, peripherals, and accessories",
            "Licensed software and applications",
            "Network access credentials",
        ],
    },
    {
        "title": "4. Procedure",
        "bullets": [
            "Assets shall remain the property of CDIPD, DUK at all times. Employees shall not have any right or interest in the assets beyond their authorised use.",
            "Employees are required to comply with the terms and conditions of asset usage as set out in this policy and in their respective employment agreements.",
            "An employee using CDIPD-provided assets is responsible for the security of those assets at all times, whether the asset is used in the office, at the employee's residence, or at any other location such as a hotel, conference room, or while travelling.",
            "Employees must ensure that assets are used exclusively for official purposes in the rightful discharge of their duties.",
            "CDIPD shall bear the cost of asset maintenance and repairs arising from normal wear and tear.",
            "Damage caused by negligence, misuse, or abuse shall be recovered from the employee.",
            "Loss of an asset due to employee negligence may result in recovery of asset cost and further penalties for the loss of sensitive information.",
            "Upon resignation or termination, the employee must return all assets in good condition. Failure to do so will result in withholding of full and final settlement and exit clearance.",
            "If an employee absconds with CDIPD assets, disciplinary proceedings may include filing a complaint with the Cyber Police.",
        ],
    },
    {
        "title": "5. Security Controls for Laptop, Desktop, Mobile, Tab, and Other Devices",
        "bullets": [
            "All laptops, desktops, mobile devices, and tablets must be protected by a username and password at all times.",
            "Employees must never leave a device unattended when using it outside the office, for example at airports, railway stations, or restaurants.",
            "When not in use, assets must be stored securely and kept out of sight, preferably locked in a sturdy cupboard or filing cabinet.",
            "Assets must never be left visibly unattended in a vehicle.",
            "Assets must be transported and stored in a padded computer bag or strong briefcase.",
            "Employees must not take any asset to an external agency or vendor for repair.",
        ],
    },
    {
        "title": "6. Data Security Controls for Laptop and Desktop",
        "bullets": [
            "You are personally accountable for all network and system access conducted under your user ID.",
            "CDIPD laptops and desktops are provided exclusively for official use by authorised employees.",
            "Never leave your laptop or desktop unattended while logged on.",
            "Employees must not install any unauthorised accessories or software that could compromise the device.",
        ],
    },
    {
        "title": "7. Asset Tracking and Verification",
        "bullets": [
            "All IT assets are recorded in a centralised Asset Tracker maintained by CDIPD and DUK.",
            "Asset Registers and Issue/Return Registers are maintained to record issuance and return.",
            "Employees are responsible for ensuring that their asset details are accurate, complete, and periodically verified.",
            "Asset verification is conducted as part of planned internal audits, inspections, and compliance activities.",
            "Deviations or instances of non-compliance are tracked and addressed through defined corrective actions.",
        ],
    },
    {
        "title": "8. Asset Return and Exit Management",
        "bullets": [
            "All assigned IT assets must be returned upon employee exit, role change, or project reallocation.",
            "Asset clearance and no-dues clearance are mandatory components of the formal exit management process.",
            "Returned assets are inspected and verified for condition and completeness by the CDIPD IT System Support team or Administration.",
        ],
    },
    {
        "title": "9. Non-Compliance and Corrective Action",
        "bullets": [
            "Non-compliance with this policy shall be treated as a process deviation.",
            "Corrective and preventive actions may be initiated in accordance with organisational guidelines.",
            "Violations of this policy may result in disciplinary action or recovery of the cost of the affected asset.",
        ],
    },
    {
        "title": "10. Asset Agreement",
        "bullets": [
            "All employees must sign the Asset Agreement Form at the time of receiving IT assets or any other assets issued by CDIPD.",
            "The Asset Agreement serves as formal confirmation of asset receipt, ownership, responsibility, and usage obligations as defined in this policy.",
            "No IT asset shall be issued to an employee without a duly signed Asset Agreement.",
            "Signed agreements shall be maintained as official records by the IT System Administrator and referenced during internal audits, CMMI assessments, and compliance reviews.",
            "Any additional assets issued subsequently shall be recorded in the agreement as and when necessary.",
        ],
    },
    {
        "title": "11. Policy Review and Validity",
        "bullets": [
            "This policy is reviewed periodically to ensure its continued suitability and effectiveness.",
            "The interpretation of Management shall be final in respect of these rules.",
            "The IT System Administration department of CDIPD reserves the right to change, alter, modify, or amend this policy at the discretion of Management.",
        ],
    },
    {
        "title": "Annexure A - Employee Acknowledgement",
        "paragraphs": [
            "I have read and understood the information contained in the Asset Usage Policy, and I hereby agree to abide by the same.",
            "Employee Name: ____________________  Employee No.: ____________________",
            "Signature: ____________________  Date: ____________________",
        ],
    },
    {
        "title": "Annexure B - CDIPD Asset Agreement",
        "paragraphs": [
            "I confirm that I have received the listed assets in good order from CDIPD and that I am responsible for all assets issued to me.",
            "Asset fields: Asset Type, Make and Model, Asset ID, Serial No., Location and Room No.",
            "Accessory fields: Charger / Power Supply and Others.",
        ],
        "bullets": [
            "The laptop, desktop, tablet, and mobile device shall remain the property of CDIPD at all times.",
            "The physical security and data security of CDIPD-provided IT assets is the personal responsibility of the employee.",
            "Keep your laptop, tablet, or mobile device in your possession and within sight at all times.",
            "Any loss, damage, theft, or malfunction must be reported immediately to the CDIPD IT System Administrator.",
            "Employees must not take any asset to an external agency or vendor for repair at any time.",
            "Employees are responsible for maintaining backup files as a precaution against data loss.",
            "Employees travelling outside India with a CDIPD laptop for a short-term period must obtain prior approval from the Director and notify the IT System Administrator and HR department.",
            "Upon returning the device, the employee must have it inspected and certified as functional by the CDIPD IT Department.",
            "Loss or damage due to negligence may result in recovery of cost or deduction from salary.",
            "Upon resignation or termination, the employee must return all assets in good condition.",
        ],
    },
]
