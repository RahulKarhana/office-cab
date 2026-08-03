from django.apps import apps


class ChatService:
    @staticmethod
    def _get_model(model_name):
        try:
            return apps.get_model("trips", model_name)
        except LookupError:
            return None

    @staticmethod
    def ensure_chat_for_stop(route_run, stop):
        PickupChat = ChatService._get_model("PickupChat")
        if PickupChat is None or route_run is None or stop is None:
            return None

        chat, _created = PickupChat.objects.get_or_create(
            route_run=route_run,
            stop=stop,
            defaults={
                "driver": route_run.driver,
                "employee": stop.employee,
                "is_active": True,
            },
        )

        if not chat.is_active:
            chat.is_active = True
            chat.save(update_fields=["is_active"])

        return chat

    @staticmethod
    def close_chat_for_stop(route_run, stop):
        PickupChat = ChatService._get_model("PickupChat")
        if PickupChat is None:
            return None

        chat = PickupChat.objects.filter(route_run=route_run, stop=stop).first()
        if chat:
            chat.is_active = False
            chat.save(update_fields=["is_active"])
        return chat

    @staticmethod
    def create_message(chat, sender, message):
        PickupChatMessage = ChatService._get_model("PickupChatMessage")
        if PickupChatMessage is None or chat is None:
            return None

        text = (message or "").strip()
        if not text:
            return None

        return PickupChatMessage.objects.create(
            chat=chat,
            sender=sender,
            message=text,
        )

    @staticmethod
    def build_chat_payload(chat):
        if not chat:
            return {
                "chat_enabled": False,
                "chat_id": None,
                "messages": [],
            }

        messages = []
        for msg in chat.messages.select_related("sender").order_by("sent_at"):
            messages.append({
                "id": msg.id,
                "sender_id": msg.sender_id,
                "sender_name": msg.sender.username if msg.sender else "",
                "message": msg.message,
                "sent_at": msg.sent_at,
            })

        return {
            "chat_enabled": chat.is_active,
            "chat_id": chat.id,
            "route_run_id": chat.route_run_id,
            "stop_id": chat.stop_id,
            "driver_id": chat.driver_id,
            "employee_id": chat.employee_id,
            "messages": messages,
        }
