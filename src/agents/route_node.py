import json
import structlog
from src.agents.state import AgentState
from src.agents.llm_router import invoke
from src.config import config

log = structlog.get_logger("router_node")

def router_node(state: AgentState) -> dict:
    """Xác định Database dựa trên câu hỏi của người dùng và config."""
    # Nếu đang trong luồng bypass (fast route), bỏ qua việc gọi LLM router
    if state.is_fast_route:
        return {"next_agent": "sql_generator"}
        
    # Nếu DB đã được set rõ ràng từ UI hoặc script evaluate, không ghi đè
    if state.current_db_path:
        return {"next_agent": "orchestrator"}
        
    question = state.user_question
    
    registry_path = config.DATA_DIR / "registry.json"
    if not registry_path.exists():
        return {"current_db_path": str(config.DB_PATH), "next_agent": "orchestrator"}
    
    with open(registry_path, 'r', encoding='utf-8') as f:
        registry = json.load(f)
        
    db_list = [f"- {db_id}: {desc}" for db_id, desc in registry.items()]
    db_list_str = "\n".join(db_list)
    
    prompt = f"""
Bạn là một Router Agent thông minh.
Hệ thống có các CSDL sau:
{db_list_str}

Câu hỏi: "{question}"

Hãy trả về CHỈ MỘT mã CSDL (db_id) phù hợp nhất với câu hỏi. 
Nếu không rõ, trả về "chinook". Không giải thích thêm.
"""
    try:
        result = invoke(
            prompt,
            temperature=0.0,
            telemetry_label="router",
        ).strip().lower()
        if result not in registry:
            result = "chinook"
        
        db_path = str(config.DATA_DIR / f"{result}.sqlite")
        return {"current_db_path": db_path, "next_agent": "orchestrator"}
    except Exception as e:
        log.error("router_error", error=str(e))
        return {"current_db_path": str(config.DB_PATH), "next_agent": "orchestrator"}
