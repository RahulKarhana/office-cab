from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from trips.utils.notification import send_push_notification
from trips.models import PickupChat
from trips.services.chat_service import ChatService


class PickupChatViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    # =========================================================
    # GET CHAT + CHECK ACCESS
    # =========================================================

    def _get_chat_for_user(
        self,
        request,
        chat_id,
    ):
        user = request.user

        chat = (
            PickupChat.objects
            .select_related(
                "route_run",
                "stop",
                "driver",
                "employee",
            )
            .filter(
                id=chat_id,
            )
            .first()
        )

        if not chat:
            return None, Response(
                {
                    "error": "Chat not found."
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        is_allowed = (
            user.is_superuser
            or getattr(
                user,
                "role",
                "",
            ) == "ADMIN"
            or chat.driver_id == user.id
            or chat.employee_id == user.id
        )

        if not is_allowed:
            return None, Response(
                {
                    "error": (
                        "You are not allowed "
                        "to access this chat."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        return chat, None

    # =========================================================
    # GET CHAT
    #
    # When driver/employee opens the chat, unread messages
    # are considered read.
    # =========================================================

    def retrieve(
        self,
        request,
        pk=None,
    ):
        chat, error_response = (
            self._get_chat_for_user(
                request,
                pk,
            )
        )

        if error_response:
            return error_response

        user = request.user

        # ---------------------------------------------
        # Mark read only for actual participants.
        # Admin opening the chat should NOT affect
        # driver/employee unread counts.
        # ---------------------------------------------

        if (
            user.id == chat.driver_id
            or user.id == chat.employee_id
        ):
            ChatService.mark_chat_read(
                chat,
                user,
            )

            # Refresh read timestamp values.
            chat.refresh_from_db()

        data = (
            ChatService.build_chat_payload(
                chat,
                current_user=user,
            )
        )

        data["current_username"] = (
            user.username
        )

        return Response(
            data,
            status=status.HTTP_200_OK,
        )

    # =========================================================
    # SEND MESSAGE
    # =========================================================

    @action(
        detail=True,
        methods=["post"],
        url_path="send-message",
    )
    def send_message(
        self,
        request,
        pk=None,
    ):
        chat, error_response = (
            self._get_chat_for_user(
                request,
                pk,
            )
        )

        if error_response:
            return error_response

        user = request.user

        # ---------------------------------------------
        # Only Driver or Employee can send messages.
        # Admin may view but cannot participate.
        # ---------------------------------------------

        if user.id not in {
            chat.driver_id,
            chat.employee_id,
        }:
            return Response(
                {
                    "error": (
                        "Only the driver and employee "
                        "can send messages in this chat."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # ---------------------------------------------
        # CLOSED CHAT
        # ---------------------------------------------

        if not chat.is_active:
            return Response(
                {
                    "error": (
                        "This chat has closed because "
                        "the pickup has been completed."
                    ),
                    "chat_enabled": False,
                    "is_closed": True,
                    "closed_at": chat.closed_at,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ---------------------------------------------
        # MESSAGE
        # ---------------------------------------------

        text = (
            request.data
            .get(
                "message",
                "",
            )
            .strip()
        )

        if not text:
            return Response(
                {
                    "error": (
                        "Message is required."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        message = (
            ChatService.create_message(
                chat=chat,
                sender=user,
                message=text,
            )
        )

        if not message:
            return Response(
                {
                    "error": (
                        "Unable to send message."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # =====================================================
        # SENDER HAS READ EVERYTHING UP TO THIS POINT
        # =====================================================

        ChatService.mark_chat_read(
            chat,
            user,
        )

        # =====================================================
        # FIND MESSAGE RECEIVER
        # =====================================================

        receiver = (
            ChatService.get_message_receiver(
                chat,
                user,
            )
        )

        # =====================================================
        # FCM
        # =====================================================

        if receiver:
            sender_role = getattr(
                user,
                "role",
                "",
            )

            if sender_role == "DRIVER":
                notification_title = (
                    "New message from your driver 💬"
                )
            elif sender_role == "EMPLOYEE":
                notification_title = (
                    "New message from employee 💬"
                )
            else:
                notification_title = (
                    "New Pickup Chat Message 💬"
                )

            send_push_notification(
                user=receiver,
                title=notification_title,
                body=message.message,
                data={
                    "type": (
                        "PICKUP_CHAT_MESSAGE"
                    ),

                    "screen": (
                        "pickup_chat"
                    ),

                    "chat_id": str(
                        chat.id
                    ),

                    "route_run_id": str(
                        chat.route_run_id
                    ),

                    "stop_id": str(
                        chat.stop_id
                    ),

                    "sender_id": str(
                        user.id
                    ),

                    "sender_name": (
                        user.username
                    ),

                    "sender_role": (
                        sender_role
                    ),
                },
            )

        # =====================================================
        # RECEIVER UNREAD COUNT
        # =====================================================

        receiver_unread_count = 0

        if receiver:
            receiver_unread_count = (
                ChatService.get_unread_count(
                    chat,
                    receiver,
                )
            )

        # =====================================================
        # RESPONSE
        # =====================================================

        return Response(
            {
                "message": (
                    "Message sent successfully."
                ),

                "chat_id": chat.id,

                "receiver_unread_count": (
                    receiver_unread_count
                ),

                "chat_message": {
                    "id": message.id,

                    "sender_id": (
                        message.sender_id
                    ),

                    "sender_name": (
                        message.sender.username
                        if message.sender
                        else ""
                    ),

                    "message": (
                        message.message
                    ),

                    "sent_at": (
                        message.sent_at
                    ),
                },
            },
            status=status.HTTP_201_CREATED,
        )

    # =========================================================
    # MARK CHAT READ
    #
    # Optional explicit endpoint.
    # Flutter can call this when needed without reloading the
    # whole chat.
    #
    # POST:
    # /api/trips/pickup-chats/<id>/mark-read/
    # =========================================================

    @action(
        detail=True,
        methods=["post"],
        url_path="mark-read",
    )
    def mark_read(
        self,
        request,
        pk=None,
    ):
        chat, error_response = (
            self._get_chat_for_user(
                request,
                pk,
            )
        )

        if error_response:
            return error_response

        user = request.user

        if user.id not in {
            chat.driver_id,
            chat.employee_id,
        }:
            return Response(
                {
                    "error": (
                        "Only chat participants "
                        "can mark messages as read."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        ChatService.mark_chat_read(
            chat,
            user,
        )

        return Response(
            {
                "success": True,
                "chat_id": chat.id,
                "unread_count": 0,
            },
            status=status.HTTP_200_OK,
        )