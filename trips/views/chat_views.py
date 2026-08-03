from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from trips.utils.notification import send_push_notification
from trips.models import PickupChat
from trips.services.chat_service import ChatService


class PickupChatViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def _get_chat_for_user(self, request, chat_id):
        user = request.user

        chat = PickupChat.objects.select_related(
            "route_run",
            "stop",
            "driver",
            "employee",
        ).filter(
            id=chat_id,
        ).first()

        if not chat:
            return None, Response(
                {"error": "Chat not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        is_allowed = (
            user.is_superuser
            or getattr(user, "role", "") == "ADMIN"
            or chat.driver_id == user.id
            or chat.employee_id == user.id
        )

        if not is_allowed:
            return None, Response(
                {"error": "You are not allowed to access this chat."},
                status=status.HTTP_403_FORBIDDEN,
            )

        return chat, None

    def retrieve(self, request, pk=None):
        chat, error_response = self._get_chat_for_user(request, pk)

        if error_response:
            return error_response

        data = ChatService.build_chat_payload(chat)
        data["current_user_id"] = request.user.id
        data["current_username"] = request.user.username

        return Response(
            data,
            status=status.HTTP_200_OK,
        )

    @action(
        detail=True,
        methods=["post"],
        url_path="send-message",
    )
    def send_message(self, request, pk=None):
        chat, error_response = self._get_chat_for_user(request, pk)

        if error_response:
            return error_response

        if not chat.is_active:
            return Response(
                {"error": "This chat is closed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        text = request.data.get("message", "").strip()

        if not text:
            return Response(
                {"error": "Message is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        message = ChatService.create_message(
            chat=chat,
            sender=request.user,
            message=text,
        )

        if not message:
            return Response(
                {"error": "Unable to send message."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        receiver = (
            chat.employee
            if request.user.id == chat.driver_id
            else chat.driver
        )

        if receiver:
            send_push_notification(
                user=receiver,
                title=f"Message from {request.user.username}",
                body=message.message,
                data={
                    "type": "PICKUP_CHAT_MESSAGE",
                    "chat_id": str(chat.id),
                    "route_run_id": str(chat.route_run_id),
                    "stop_id": str(chat.stop_id),
                    "sender_id": str(request.user.id),
                    "sender_name": request.user.username,
                    "screen": "pickup_chat",
                },
            )

        return Response(
            {
                "message": "Message sent successfully.",
                "chat_message": {
                    "id": message.id,
                    "sender_id": message.sender_id,
                    "sender_name": (
                        message.sender.username
                        if message.sender
                        else ""
                    ),
                    "message": message.message,
                    "sent_at": message.sent_at,
                },
            },
            status=status.HTTP_201_CREATED,
        )