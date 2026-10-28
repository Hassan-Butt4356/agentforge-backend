from django.contrib import admin
from .models import Conversation, Message


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ["id", "role", "content", "created_at"]


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ["title", "user", "agent", "created_at"]
    search_fields = ["title", "user__username", "agent__name"]
    readonly_fields = ["id", "created_at"]
    inlines = [MessageInline]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ["role", "conversation", "created_at"]
    list_filter = ["role"]
    readonly_fields = ["id", "created_at"]
