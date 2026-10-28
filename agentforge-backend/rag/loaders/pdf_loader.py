import logging
from pathlib import Path

from llama_index.core import SimpleDirectoryReader
from llama_index.core.schema import Document as LlamaDocument

logger = logging.getLogger(__name__)


def load_pdf(file_path: str) -> list[LlamaDocument]:
    """
    Load a single PDF file into LlamaIndex Document objects.

    SimpleDirectoryReader handles text extraction automatically.
    Each page becomes a separate Document with metadata
    (filename, page number, file size).

    Args:
        file_path: Absolute path to the PDF on disk.

    Returns:
        List of LlamaDocument objects (one per page typically).
    """
    path = Path(file_path)
    if not path.exists():
        logger.error("PDF not found at path: %s", file_path)
        return []

    reader = SimpleDirectoryReader(input_files=[str(path)])
    documents = reader.load_data()
    logger.info("Loaded %d page(s) from '%s'", len(documents), path.name)
    return documents
