"""orc.messaging — Telegram and message-parsing subpackage.

Re-exports the complete public API from both modules so that existing code
importing ``from orc import telegram as tg`` or
``from orc.telegram_messages import ...`` continues to work unchanged
after tests are migrated to the new paths.
"""

from orc.messaging import messages, telegram

__all__ = ["telegram", "messages"]
