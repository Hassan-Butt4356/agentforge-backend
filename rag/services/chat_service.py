import logging
from collections.abc import Iterable, Iterator

from llama_index.core.llms import ChatMessage, MessageRole

from rag.indexes.vector_index import load_index, configure_llama_settings

from rag.agents.base_agent import build_rag_agent

logger = logging.getLogger(__name__)

NO_ANSWER = "I could not find relevant information for your question."


SNIPPET_CHARS = 400


class ChatServiceError(Exception):
    """The LLM or vector store failed — no answer could be generated."""


ANSWER_MARKER = "Answer: "


def _strip_react_preamble(deltas: Iterable[str]) -> Iterator[str]:
    """
    LlamaIndex's ReAct agent starts streaming its final turn at the chunk that
    completes the "Answer: " marker, so the first delta can still begin with the
    tail of that marker ("wer: Hello", " Hello"). Drop that tail from the first
    non-empty delta and pass everything else through unchanged.
    """
    first = True
    for delta in deltas:
        if first and delta:
            first = False
            if ANSWER_MARKER in delta:
                delta = delta.split(ANSWER_MARKER, 1)[1]
            else:
                # smallest k such that the delta opens with the marker's last len-k chars
                for k in range(1, len(ANSWER_MARKER)):
                    if delta.startswith(ANSWER_MARKER[k:]):
                        delta = delta[len(ANSWER_MARKER) - k:]
                        break
            if not delta:
                continue
        yield delta


def _extract_sources(response) -> list[dict]:
    """
    Turn the retrieved chunks behind a response into citation dicts:
    {document_id, filename, page, snippet, score}. Chunks of the same
    document page are collapsed, keeping the best-scoring one.
    """
    best: dict[tuple, dict] = {}
    for node_with_score in getattr(response, "source_nodes", None) or []:
        node = node_with_score.node
        meta = node.metadata or {}
        source = {
            "document_id": meta.get("document_id"),
            "filename": meta.get("file_name") or "Unknown document",
            "page": meta.get("page_label"),
            "snippet": node.get_content().strip()[:SNIPPET_CHARS],
            "score": node_with_score.score,
        }
        key = (source["document_id"] or source["filename"], source["page"])
        current = best.get(key)
        if current is None or (source["score"] or 0) > (current["score"] or 0):
            best[key] = source
    return sorted(best.values(), key=lambda s: s["score"] or 0, reverse=True)


class ChatService:
    """
    Wraps the ReActAgent for multi-turn conversation.

    The agent is built lazily on first use (avoids loading ChromaDB on import).
    Each ChatService instance is scoped to one agent — one instance per request.

    In production with many concurrent users you'd cache the built agent
    (or the loaded VectorStoreIndex) in Redis/Django cache keyed by agent.id
    to avoid rebuilding it on every single request.
    """

    def __init__(self, agent_model):
        self.agent_model = agent_model
        self._agent = None

    def _load_agent(self):
        if self._agent is None:
            self._agent = build_rag_agent(self.agent_model)
        return self._agent

    def _build_chat_history(self, history: list[dict]) -> list[ChatMessage]:
        messages = []
        for msg in history:
            role = MessageRole.USER if msg["role"] == "user" else MessageRole.ASSISTANT
            messages.append(ChatMessage(role=role, content=msg["content"]))
        return messages

    def chat(self, user_message: str, history: list[dict]) -> tuple[str, list[dict]]:
        """
        Returns (answer, sources): the assistant's answer and the passages it was
        grounded on (see _extract_sources).
        Raises ChatServiceError when the LLM / vector store can't produce one,
        so the view can return a real error status instead of a fake answer.
        """
        try:
            agent = self._load_agent()
            chat_history = self._build_chat_history(history)
            response = agent.chat(user_message, chat_history=chat_history)
            answer = str(response).strip()
            if not answer:
                return NO_ANSWER, []
            return answer, _extract_sources(response)

        except ValueError as e:
            if "Reached max iterations" not in str(e):
                logger.error("ChatService error: %s", e, exc_info=True)
                raise ChatServiceError(str(e)) from e

            return self._fallback_answer(user_message)

        except Exception as e:
            logger.error("ChatService error: %s", e, exc_info=True)
            raise ChatServiceError(str(e)) from e

    def _fallback_answer(self, user_message: str) -> tuple[str, list[dict]]:
        """
        ReActAgent found the answer but hit max_iterations before finalising.
        Fall back to a plain query engine over the same collection.
        """
        logger.warning("ReActAgent hit max iterations — falling back to query engine.")
        try:
            configure_llama_settings()
            index = load_index(self.agent_model.chroma_collection_name)
            response = index.as_query_engine(similarity_top_k=6).query(user_message)
            answer = str(response).strip()
            return (answer, _extract_sources(response)) if answer else (NO_ANSWER, [])
        except Exception as fallback_error:
            logger.error("Fallback query failed: %s", fallback_error, exc_info=True)
            raise ChatServiceError(str(fallback_error)) from fallback_error

    def stream_chat(self, user_message: str, history: list[dict]) -> Iterator[tuple[str, object]]:
        """
        Like chat(), but yields events as the answer is produced:
          ("status", text)            progress note, sent before the slow retrieval step
          ("token", text)             next piece of the answer
          ("done", (answer, sources)) final, complete answer and its citations
        Raises ChatServiceError on failure (possibly after some tokens were yielded).
        """
        yield "status", "Searching the documents…"
        try:
            agent = self._load_agent()
            response = agent.stream_chat(
                user_message, chat_history=self._build_chat_history(history)
            )
            if getattr(response, "is_dummy_stream", False):
                # Tool-free answer that is already complete: skip the fake word-by-word replay.
                tokens = iter([str(response.response)])
            else:
                tokens = _strip_react_preamble(response.response_gen)

            parts: list[str] = []
            for token in tokens:
                parts.append(token)
                yield "token", token
            answer = "".join(parts).strip()
            if not answer:
                yield "token", NO_ANSWER
                yield "done", (NO_ANSWER, [])
                return
            yield "done", (answer, _extract_sources(response))

        except ValueError as e:
            if "Reached max iterations" not in str(e):
                logger.error("ChatService stream error: %s", e, exc_info=True)
                raise ChatServiceError(str(e)) from e
            answer, sources = self._fallback_answer(user_message)
            yield "token", answer
            yield "done", (answer, sources)

        except ChatServiceError:
            raise
        except Exception as e:
            logger.error("ChatService stream error: %s", e, exc_info=True)
            raise ChatServiceError(str(e)) from e
