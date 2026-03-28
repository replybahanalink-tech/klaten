import asyncio
import json
import os

# Install playwright browser on startup (Required for Hugging Face Spaces)
os.system("python -m playwright install chromium")
os.system("python -m playwright install-deps chromium")

import gradio as gr
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src.agent.graph import build_graph, SYSTEM_PROMPT

# Load environment variables
load_dotenv()


async def run_agent_ui(save_to_db: bool):
    """Run the LangGraph agent and yield UI updates for Gradio."""
    user_message = "Ambil data Target dan Realisasi PAD dari halaman https://dashboard.etax-klaten.id/monitoring_realisasi"
    
    # Initialize the graph
    graph = build_graph()
    
    # LangGraph initial state with system prompt
    initial_state = {
        "messages": [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=user_message)
        ],
        "hasil_data": ""
    }
    
    # Configuration to pass to tools
    config = {"configurable": {"save_to_db": save_to_db}}
    
    logs = ""
    json_result = {}
    
    header = "💾 Mode: GET & SAVE TO DATABASE" if save_to_db else "🔍 Mode: GET DATA ONLY"
    logs += f"📝 Request: {user_message}\n{header}\n\n⏳ Agent sedang bekerja...\n\n"
    yield logs, None
    
    try:
        # Stream events from the graph
        async for event in graph.astream(initial_state, config=config):
            for node_name, state_update in event.items():
                if "messages" in state_update:
                    for msg in state_update["messages"]:
                        # If agent decides to call a tool
                        if isinstance(msg, AIMessage):
                            if msg.tool_calls:
                                for tc in msg.tool_calls:
                                    logs += f"🔧 Agent memanggil tool: {tc['name']}({tc['args']})\n"
                                    yield logs, []
                            elif msg.content:
                                logs += f"🤖 Agent merespons: {msg.content}\n"
                                yield logs, []
                                
                        # If a tool finishes executing
                        elif isinstance(msg, ToolMessage):
                            content_str = str(msg.content)
                            # Truncate long JSON to keep log readable
                            out_preview = content_str[:150] + "... [TRUNCATED]" if len(content_str) > 150 else content_str
                            logs += f"📋 Hasil tool ({msg.name}): {out_preview}\n\n"
                            yield logs, []
                            
                            # Capture the final JSON from our scraper tool
                            if msg.name == "scrape_pad_realisasi":
                                try:
                                    json_result = json.loads(content_str)
                                except Exception:
                                    pass
                        
        # Prepare table data if JSON exists
        table_data = []
        if json_result and "data_target_realisasi_pad" in json_result:
            for item in json_result["data_target_realisasi_pad"]:
                table_data.append([
                    item.get("no", ""),
                    item.get("jenis_pajak", ""),
                    item.get("target_rp", 0),
                    item.get("realisasi_rp", 0),
                    item.get("persentase", "")
                ])
                
        logs += "✅ Proses Selesai!"
        yield logs, table_data
        
    except Exception as e:
        logs += f"\n❌ Terjadi error: {str(e)}"
        yield logs, []


def create_ui():
    """Build the Gradio blocks UI."""
    with gr.Blocks(title="PAD Data Scraper") as app:
        gr.Markdown("# 🤖 AI Agent - PAD Data Scraper")
        gr.Markdown(
            "Agent AI ini menggunakan browser *headless* untuk membaca tabel web "
            "`dashboard.etax-klaten.id`, mengekstrak data, dan bisa menyimpannya secara "
            "otomatis ke database PostgreSQL milik Anda."
        )
        
        with gr.Row():
            btn_get = gr.Button("🔍 Get Data (No DB Save)", variant="secondary")
            btn_save = gr.Button("💾 Get & Save (Simpan ke DB)", variant="primary")
            
        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown("### 📡 Live Agent Logs")
                log_box = gr.Textbox(
                    lines=20, 
                    label="Agent Activity", 
                    interactive=False, 
                    max_lines=20,
                    autoscroll=True
                )
            
            with gr.Column(scale=1):
                gr.Markdown("### 📊 Extracted Data")
                data_table = gr.Dataframe(
                    headers=["No", "Jenis Pajak", "Target (Rp)", "Realisasi (Rp)", "Persentase (%)"],
                    datatype=["number", "str", "number", "number", "str"],
                    label="Tabel Target dan Realisasi",
                    interactive=False
                )
                
        # Connect buttons to the async generator
        btn_get.click(
            fn=run_agent_ui,
            inputs=[gr.State(False)],
            outputs=[log_box, data_table]
        )
        
        btn_save.click(
            fn=run_agent_ui,
            inputs=[gr.State(True)],
            outputs=[log_box, data_table]
        )
        
    return app


# Hugging Face Spaces requires the Gradio app to be bound to a top-level variable named `app` or `demo`.
app = create_ui()

if __name__ == "__main__":
    app.launch(server_name="0.0.0.0", server_port=7860, theme=gr.themes.Soft())
