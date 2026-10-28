import logging

from llama_index.core.llms import ChatMessage, MessageRole

from rag.indexes.vector_index import load_index, configure_llama_settings

from rag.agents.base_agent import build_rag_agent

logger = logging.getLogger(__name__)

NO_ANSWER = "I could not find relevant information for your question."


class ChatServiceError(Exception):
    """The LLM or vector store failed — no answer could be generated."""


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

    def chat(self, user_message: str, history: list[dict]) -> str:
        """
        Returns the assistant's answer.
        Raises ChatServiceError when the LLM / vector store can't produce one,
        so the view can return a real error status instead of a fake answer.
        """
        try:
            agent = self._load_agent()
            chat_history = self._build_chat_history(history)
            response = agent.chat(user_message, chat_history=chat_history)
            answer = str(response).strip()
            if not answer:
                return NO_ANSWER
            return answer

        except ValueError as e:
            if "Reached max iterations" not in str(e):
                logger.error("ChatService error: %s", e, exc_info=True)
                raise ChatServiceError(str(e)) from e

            # ReActAgent found the answer but hit max_iterations before finalising.
            # Fall back to a plain query engine over the same collection.
            logger.warning("ReActAgent hit max iterations — falling back to query engine.")
            try:
                configure_llama_settings()
                index = load_index(self.agent_model.chroma_collection_name)
                response = index.as_query_engine(similarity_top_k=6).query(user_message)
                return str(response).strip() or NO_ANSWER
            except Exception as fallback_error:
                logger.error("Fallback query failed: %s", fallback_error, exc_info=True)
                raise ChatServiceError(str(fallback_error)) from fallback_error

        except Exception as e:
            logger.error("ChatService error: %s", e, exc_info=True)
            raise ChatServiceError(str(e)) from e
