"""
The agent: connects all the parts into one turn pipeline.

WHERE THIS FITS:
    This is the single entry point for the UI (and for the tests).
    The UI sends a caller message. The agent returns the reply and the debug data.
    The UI does not know about phases, tools, or prompts. It only calls handle_turn().

THE TURN PIPELINE (each caller message goes through these steps in order):

    caller message
        |
        v
    1. extraction.extract_facts()   LLM   "What did the caller say?"      -> ExtractedFacts
        |
        v
    2. controller.advance()         code  "What does the SOP allow now?"  -> TurnPlan (+ state changes, tool calls)
        |
        v
    3. responder.write_reply()      LLM   "How do we say it naturally?"   -> reply text
        |
        v
    4. output_guard.check_reply()   code  "Is the reply safe?"            -> final reply text
        |
        v
    reply to the caller (+ debug data for the UI panel)

    Two LLM calls per turn. All decisions are in the code steps (2 and 4).
"""

from pydantic import BaseModel

from .config import Settings
from .controller import SopController, TurnPlan
from .data_store import DataStore
from .extraction import ExtractedFacts, extract_facts
from .guidance import DocumentGuidance
from .llm_client import LLMClient, LLMError, make_llm_client
from .output_guard import GuardResult, check_reply
from .responder import write_reply
from .state import ChatTurn, SessionState
from .tools import ToolBox

GREETING = (
    "Hello, thank you for contacting claims support. I can help you with your insurance claims. "
    "How can I help you today?"
)
TECHNICAL_PROBLEM = "Sorry, I have a technical problem right now. Could you please send your message again?"


class AgentReply(BaseModel):
    """What the UI gets back after one turn."""

    text: str                  # The reply to show to the caller.
    # Data for the debug panel: phase, memory, verification status, events, tool calls.
    debug: dict = {}


class SopAgent:
    """Owns the shared parts (data, tools, LLM). One instance for the app (or one for each API key)."""

    def __init__(self, settings: Settings, llm: LLMClient | None = None) -> None:
        """
        Build once at start-up.
        Args:
            settings: The app settings.
            llm: Optional. Give a fake client in tests. If None, make the client from the settings.
        """
        self.settings = settings
        self.store = DataStore(settings.fixtures_dir)
        self.guidance = DocumentGuidance(self.store.document_guidelines)
        self.tools = ToolBox(self.store, self.guidance, settings)
        self.llm = llm or make_llm_client(settings)
        self.controller = SopController(self.tools, self.llm)

    @staticmethod
    def new_session() -> SessionState:
        """Start a new conversation. Send the greeting as the first message."""
        state = SessionState()
        state.history.append(ChatTurn(role="assistant", text=GREETING))
        return state

    def handle_turn(self, state: SessionState, message: str) -> AgentReply:
        """Run the full pipeline for one caller message, updating state in place."""
        state.history.append(ChatTurn(role="user", text=message))
        events_before = len(state.events)

        try:
            facts = extract_facts(message, state, self.llm, today=self.settings.demo_today)  # 1. understand
        except LLMError as error:
            # Without facts we cannot decide safely. Change nothing, and ask the caller to repeat.
            state.events.append(f"extraction failed: {error}")
            state.history.append(ChatTurn(role="assistant", text=TECHNICAL_PROBLEM))
            return AgentReply(text=TECHNICAL_PROBLEM, debug={"phase": state.phase.value, "error": str(error),
                                                             "this_turn": {"events": state.events[events_before:]}})
        plan = self.controller.advance(state, facts)                                          # 2. decide
        try:
            reply = write_reply(plan, state, self.llm)                                        # 3. phrase
        except LLMError as error:
            state.events.append(f"responder failed: {error}")
            reply = TECHNICAL_PROBLEM
        guard = check_reply(reply, state, self.store)                                     # 4. check

        state.history.append(ChatTurn(role="assistant", text=guard.reply))
        return AgentReply(
            text=guard.reply,
            debug=self.debug_view(state, facts, plan, guard, state.events[events_before:]),
        )

    def debug_view(
        self, state: SessionState, facts: ExtractedFacts, plan: TurnPlan, guard: GuardResult, new_events: list[str]
    ) -> dict:
        """
        A small dictionary for the debug panel in the UI.
        This panel also displays the state to the user.
        """
        return {
            "phase": state.phase.value,
            "verified": state.is_verified(),
            "verified_party_id": state.verified_party_id,
            "caller_role": state.caller_role.value,
            "identity_given": state.identity.model_dump(exclude_none=True),
            "memory": state.memory.model_dump(exclude_defaults=True),
            "selected_case_id": state.selected_case_id,
            "off_topic_strikes": state.off_topic_strikes,
            **({"sent_email": state.sent_email} if state.sent_email else {}),
            "this_turn": {
                "events": new_events,
                "plan_goal": plan.goal,
                "guard_ok": guard.ok,
                "extracted": facts.model_dump(exclude_defaults=True),
            },
        }
