# run.py
from app import create_app
from app.extensions import db
from app.models.user import User
from app.models.transaction import Transaction
from app.models.instalment import InstalmentPlan
from app.models.instalment_payment import InstalmentPayment
from app.models.dispute import Dispute
from app.models.document import Document
from app.models.notification_settings import NotificationSetting
from app.models.system_settings import SystemSetting, TransactionCharge
from app.models.support_ticket import SupportTicket, TicketMessage

from app.services.system_defaults import init_system_settings

app = create_app()

if __name__ == "__main__":
    with app.app_context():
        # Create all tables
        db.create_all()
        print("✅ Database tables created successfully")

        # Initialize system settings
        init_system_settings()
        print("✅ System settings initialized")

    # Development server only. In production run: gunicorn "app:create_app()"
    import os

    print("🚀 Starting Tabital Pay Server...")
    app.run(
        debug=app.config.get("DEBUG", False),
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", 5000)),
    )
