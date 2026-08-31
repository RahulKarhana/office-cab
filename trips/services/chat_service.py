from django.apps import apps
from django.utils import timezone


class ChatService:
    @staticmethod
    def _get_model(model_name):
        try:
            return apps.get_model(
                "trips",
                model_name,
            )
        except LookupError:
            return None

    # =========================================================
    # CREATE / GET CHAT FOR CURRENT PICKUP STOP
    # =========================================================

    @staticmethod
    def ensure_chat_for_stop(route_run, stop):
        PickupChat = ChatService._get_model(
            "PickupChat"
        )

        if (
            PickupChat is None
            or route_run is None
            or stop is None
        ):
            return None

        chat, created = (
            PickupChat.objects.get_or_create(
                route_run=route_run,
                stop=stop,
                defaults={
                    "driver": route_run.driver,
                    "employee": stop.employee,
                    "is_active": True,
                },
            )
        )

        # IMPORTANT:
        # Never reopen a chat after Pickup Done / No Show.
        if not created and not chat.is_active:
            return chat

        return chat

    # =========================================================
    # CLOSE CHAT
    # =========================================================

    @staticmethod
    def close_chat_for_stop(route_run, stop):
        PickupChat = ChatService._get_model(
            "PickupChat"
        )

        if PickupChat is None:
            return None

        chat = PickupChat.objects.filter(
            route_run=route_run,
            stop=stop,
        ).first()

        if chat and chat.is_active:
            chat.close_chat()

        return chat

    # =========================================================
    # CREATE MESSAGE
    # =========================================================

    @staticmethod
    def create_message(
        chat,
        sender,
        message,
    ):
        PickupChatMessage = (
            ChatService._get_model(
                "PickupChatMessage"
            )
        )

        if (
            PickupChatMessage is None
            or chat is None
            or sender is None
        ):
            return None

        # Closed chats cannot receive new messages.
        if not chat.is_active:
            return None

        # Only this chat's driver or employee
        # is allowed to send.
        if sender.id not in {
            chat.driver_id,
            chat.employee_id,
        }:
            return None

        text = (message or "").strip()

        if not text:
            return None

        return PickupChatMessage.objects.create(
            chat=chat,
            sender=sender,
            message=text,
        )

    # =========================================================
    # BUILD CHAT PAYLOAD
    # =========================================================

    @staticmethod
    def build_chat_payload(
        chat,
        current_user=None,
    ):
        if not chat:
            return {
                "chat_enabled": False,
                "is_closed": True,
                "closed_reason": (
                    "Chat is not available."
                ),
                "chat_id": None,
                "route_run_id": None,
                "stop_id": None,
                "driver_id": None,
                "employee_id": None,
                "current_user_id": (
                    current_user.id
                    if current_user
                    else None
                ),
                "unread_count": 0,
                "messages": [],
            }

        messages_qs = (
            chat.messages
            .select_related("sender")
            .order_by("sent_at")
        )

        messages = []

        for msg in messages_qs:
            messages.append(
                {
                    "id": msg.id,
                    "sender_id": (
                        msg.sender_id
                    ),
                    "sender_name": (
                        msg.sender.username
                        if msg.sender
                        else ""
                    ),
                    "message": msg.message,
                    "sent_at": msg.sent_at,
                }
            )

        unread_count = (
            ChatService.get_unread_count(
                chat,
                current_user,
            )
        )

        return {
            "chat_enabled": chat.is_active,

            "is_closed": (
                not chat.is_active
            ),

            "closed_reason": (
                None
                if chat.is_active
                else (
                    "This chat has closed because "
                    "the pickup has been completed."
                )
            ),

            "closed_at": chat.closed_at,

            "chat_id": chat.id,

            "route_run_id": (
                chat.route_run_id
            ),

            "stop_id": chat.stop_id,

            "driver_id": chat.driver_id,

            "employee_id": (
                chat.employee_id
            ),

            "current_user_id": (
                current_user.id
                if current_user
                else None
            ),

            "unread_count": unread_count,

            "messages": messages,
        }

    # =========================================================
    # GET UNREAD COUNT
    # =========================================================

    @staticmethod
    def get_unread_count(
        chat,
        user,
    ):
        if not chat or not user:
            return 0

        messages_qs = chat.messages.all()

        # Employee unread messages =
        # messages sent by driver.
        if user.id == chat.employee_id:
            unread_qs = messages_qs.filter(
                sender_id=chat.driver_id,
            )

            if chat.employee_last_read_at:
                unread_qs = unread_qs.filter(
                    sent_at__gt=(
                        chat.employee_last_read_at
                    ),
                )

            return unread_qs.count()

        # Driver unread messages =
        # messages sent by employee.
        if user.id == chat.driver_id:
            unread_qs = messages_qs.filter(
                sender_id=chat.employee_id,
            )

            if chat.driver_last_read_at:
                unread_qs = unread_qs.filter(
                    sent_at__gt=(
                        chat.driver_last_read_at
                    ),
                )

            return unread_qs.count()

        return 0

    # =========================================================
    # MARK CHAT READ
    # =========================================================

    @staticmethod
    def mark_chat_read(
        chat,
        user,
    ):
        if not chat or not user:
            return False

        now = timezone.now()

        if user.id == chat.employee_id:
            chat.employee_last_read_at = now

            chat.save(
                update_fields=[
                    "employee_last_read_at",
                ]
            )

            return True

        if user.id == chat.driver_id:
            chat.driver_last_read_at = now

            chat.save(
                update_fields=[
                    "driver_last_read_at",
                ]
            )

            return True

        return False

    # =========================================================
    # GET OTHER CHAT PARTICIPANT
    #
    # Useful for FCM when a new message is sent.
    # =========================================================

    @staticmethod
    def get_message_receiver(
        chat,
        sender,
    ):
        if not chat or not sender:
            return None

        if sender.id == chat.driver_id:
            return chat.employee

        if sender.id == chat.employee_id:
            return chat.driver

        return None