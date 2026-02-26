import asyncio
import logging
import os
import sys
import json

# Ensure backend directory is in python path
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
project_root = os.path.dirname(backend_dir)
sys.path.append(project_root)

from dotenv import load_dotenv
load_dotenv(os.path.join(backend_dir, '.env'))

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes
from backend.models.payment import PaymentTransaction
from backend.services.subscription_service import activate_user_subscription

# Configure logging
log_file = os.path.join(backend_dir, 'telegram_bot.log')
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Parses the CallbackQuery and updates the message text."""
    query = update.callback_query
    await query.answer() # Ack the interaction

    data = query.data
    action, ref = data.split(":")
    
    logger.info(f"Received callback: {action} for ref {ref}")

    txn = PaymentTransaction.get_transaction(ref)
    if not txn:
        await query.edit_message_text(text=f"❌ Transaction {ref} not found in DB.")
        return

    if txn.get("status") == "completed" and action == "approve":
        await query.edit_message_text(text=f"✅ Transaction {ref} was already approved.")
        return

    try:
        if action == "approve":
            user_id = txn.get("user_id")
            tier = txn.get("tier", "standard")
            amount = txn.get("amount", 0)
            
            # Activate Subscription
            await activate_user_subscription(user_id, tier, amount)
            
            # Update DB
            PaymentTransaction.update_transaction(
                reference=ref,
                status="completed",
                order_no=f"TELEGRAM-{ref}"
            )
            
            admin_username = update.effective_user.username or update.effective_user.first_name

            # Extract Metadata
            metadata_raw = txn.get("metadata", {})
            if isinstance(metadata_raw, str):
                try:
                    metadata = json.loads(metadata_raw)
                except:
                    metadata = {}
            else:
                metadata = metadata_raw if metadata_raw else {}
            
            sender_name = metadata.get("sender_name", "Unknown")
            email = metadata.get("email", "N/A")
            created_at = txn.get("created_at", "Unknown Date")
            
            msg = (
                f"✅ **APPROVED**\n\n"
                f"👤 **User:** `{user_id}`\n"
                f"📧 **Email:** `{email}`\n"
                f"💰 **Amount:** ₦{amount:,.2f}\n"
                f"🏦 **Sender:** {sender_name}\n"
                f"📅 **Time:** {created_at}\n"
                f"🔖 **Ref:** `{ref}`\n\n"
                f"👮 **Approved By:** @{admin_username}"
            )
            await query.edit_message_text(text=msg, parse_mode="Markdown")
            
        elif action == "reject":
            PaymentTransaction.update_transaction(
                reference=ref,
                status="rejected"
            )
            admin_username = update.effective_user.username or update.effective_user.first_name
            await query.edit_message_text(text=f"❌ **REJECTED**\n\nRef: `{ref}`\nAdmin: {admin_username}", parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error processing {action} for {ref}: {e}")
        await query.edit_message_text(text=f"⚠️ Error processing request: {str(e)}")


def main():
    """Run the bot."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set in environment")
        return

    application = Application.builder().token(token).build()

    application.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Starting Telegram Bot Worker...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
