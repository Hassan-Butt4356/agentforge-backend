from django.contrib import admin
from .models import Agent, Document


class DocumentInline(admin.TabularInline):
    model = Document
    extra = 0
    readonly_fields = ["id", "filename", "ingested", "task_id", "uploaded_at"]


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = ["name", "owner", "is_public", "is_indexed", "indexing_status", "created_at"]
    list_filter = ["is_public", "is_indexed", "indexing_status"]
    search_fields = ["name", "owner__username"]
    readonly_fields = ["id", "created_at", "updated_at"]
    inlines = [DocumentInline]


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ["filename", "agent", "ingested", "task_id", "uploaded_at"]
    list_filter = ["ingested"]
    readonly_fields = ["id", "uploaded_at"]
