import os
import logging
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

logger = logging.getLogger(__name__)

class TelegramService:
    def __init__(self):
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.admin_chat_id = os.getenv("TELEGRAM_ADMIN_CHAT_ID")
        self.bot = None

    async def get_bot(self):
        if not self.bot and self.bot_token:
            self.bot = Bot(token=self.bot_token)
        return self.bot

    async def send_verification_request(self, transaction_data: dict):
        """
        Send a verification card to the admin channel with Approve/Reject buttons
        """
        try:
            bot = await self.get_bot()
            if not bot or not self.admin_chat_id:
                logger.warning("Telegram Bot Token or Admin Chat ID not set")
                return

            ref = transaction_data.get("reference")
            amount = transaction_data.get("amount")
            tier = transaction_data.get("tier")
            sender = transaction_data.get("sender_name", "Unknown")
            email = transaction_data.get("email", "N/A")

            message = (
                f"🚨 **PAYMENT VERIFICATION NEEDED** 🚨\n\n"
                f"💰 **Amount**: ₦{amount:,.2f}\n"
                f"👤 **Sender**: {sender}\n"
                f"📧 **Email**: {email}\n"
                f"🏷️ **Tier**: {tier}\n"
                f"🆔 **Ref**: `{ref}`\n\n"
                f"Action Required:"
            )

            keyboard = [
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"approve:{ref}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"reject:{ref}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await bot.send_message(
                chat_id=self.admin_chat_id,
                text=message,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
            logger.info(f"Telegram verification sent for {ref}")

        except TelegramError as e:
            logger.error(f"Failed to send Telegram message: {e}")
        except Exception as e:
            logger.error(f"Telegram Service Error: {e}")

    async def send_notification(self, message: str):
        try:
            bot = await self.get_bot()
            if not bot or not self.admin_chat_id:
                return

            await bot.send_message(
                chat_id=self.admin_chat_id,
                text=message
            )
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")

telegram_service = TelegramService()
