"""
The test UI: a Gradio chat page with a debug panel. No business logic here.

Run: .venv/bin/python app.py, then open http://127.0.0.1:7860.
Gradio builds the web page from Python. gr.State keeps one SessionState per browser tab.
Event handlers (on_send, new_conversation) get the input values and return the output values.
"""

import gradio as gr

from sop_agent.agent import SopAgent
from sop_agent.config import PROVIDERS, load_settings, with_model
from sop_agent.llm_client import LLMError, make_llm_client
from sop_agent.state import SessionState

SETTINGS = load_settings()
NO_KEY_MESSAGE = "No API key for this provider. Paste an API key into the 'API key' field."

# One agent per (provider, model, key).
_agents: dict[tuple, SopAgent] = {}

EXAMPLES = [
    "I'm the policyholder. My name is Margaret Chen, policy POL-9921. I'm calling about my denied "
    "healthcare claim from January. DOB is 1985-03-15, SSN last four is 4472.",
    "Hi, I'm David Chen, calling for my mother Margaret Chen.",
    "What is reinforcement learning?",
]


def get_agent(provider: str, model: str, api_key: str) -> SopAgent | None:
    """Values from the page win. Empty values fall back to the server settings for that provider."""
    settings = with_model(SETTINGS, provider, model.strip() or None, api_key.strip() or None)
    if not settings.llm_api_key:
        return None
    cache_key = (settings.llm_provider, settings.llm_model, settings.llm_api_key)
    if cache_key not in _agents:
        _agents[cache_key] = SopAgent(settings, llm=make_llm_client(settings))
    return _agents[cache_key]


def to_chat(session: SessionState) -> list[dict]:
    return [{"role": turn.role, "content": turn.text} for turn in session.history]


def new_conversation():
    session = SopAgent.new_session()
    return to_chat(session), session, {"phase": session.phase.value}


def on_send(message: str, session: SessionState, provider: str, model: str, api_key: str):
    # The session is missing if the server restarted (the free host sleeps) or the page did not finish loading.
    session = session or SopAgent.new_session()
    if not message.strip():
        return to_chat(session), session, gr.skip(), ""
    try:
        agent = get_agent(provider, model, api_key)
    except LLMError as error:
        agent, problem = None, str(error)
    else:
        problem = NO_KEY_MESSAGE
    if agent is None:
        chat = to_chat(session) + [{"role": "user", "content": message}, {"role": "assistant", "content": problem}]
        return chat, session, {"error": problem}, message
    reply = agent.handle_turn(session, message)
    return to_chat(session), session, reply.debug, ""


def build_ui() -> gr.Blocks:
    with gr.Blocks(title="Insurance Claims SOP Agent") as page:
        gr.Markdown(
            "## Insurance claims support agent\n"
            "The agent follows a fixed SOP: **VERIFY_ID → RESOLVE_INTENT → PROCESS_CASE → POST_PROCESS**. "
            "The debug panel on the right shows the phase, the memory, and the decisions of each turn."
        )
        session = gr.State()
        with gr.Row():
            with gr.Column(scale=3):
                chat = gr.Chatbot(label="Conversation", height=520)
                message = gr.Textbox(label="Your message", placeholder="Type a message and press Enter", lines=2)
                with gr.Row():
                    send = gr.Button("Send", variant="primary")
                    reset = gr.Button("New conversation")
                gr.Examples(examples=EXAMPLES, inputs=message, label="Example messages")
            with gr.Column(scale=2):
                with gr.Accordion("Model settings (optional)", open=False):
                    provider = gr.Dropdown(choices=list(PROVIDERS), value=SETTINGS.llm_provider, label="Provider")
                    model = gr.Textbox(label="Model", placeholder=f"Empty = default ({SETTINGS.llm_model})")
                    api_key = gr.Textbox(
                        label="API key", placeholder="Empty = the server's key for this provider", type="password"
                    )
                debug = gr.JSON(label="Debug panel (SOP state)")

        page.load(new_conversation, outputs=[chat, session, debug])
        reset.click(new_conversation, outputs=[chat, session, debug])
        inputs = [message, session, provider, model, api_key]
        send.click(on_send, inputs=inputs, outputs=[chat, session, debug, message])
        message.submit(on_send, inputs=inputs, outputs=[chat, session, debug, message])
    return page


if __name__ == "__main__":
    build_ui().launch(show_error=True)
