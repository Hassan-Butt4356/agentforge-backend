from django.conf import settings
from rest_framework.exceptions import ValidationError

PDF_MAGIC = b"%PDF-"


def validate_pdf(f) -> None:
    """
    Reject anything that isn't a real PDF before it is saved or queued.
    Checks the extension AND the file signature — a renamed .txt fails.
    """
    if not f.name.lower().endswith(".pdf"):
        raise ValidationError({"files": f"'{f.name}' is not a PDF."})

    if f.size > settings.FILE_UPLOAD_MAX_MEMORY_SIZE:
        raise ValidationError({"files": f"'{f.name}' exceeds the 50 MB limit."})

    header = f.read(len(PDF_MAGIC))
    f.seek(0)
    if header != PDF_MAGIC:
        raise ValidationError({"files": f"'{f.name}' is not a valid PDF file."})
