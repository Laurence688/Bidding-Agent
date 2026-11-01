"""SOC 投标助手：本机、证据优先、人工确认。"""

# 在导入 LangChain/LangGraph 前显式关闭遗留环境中的云追踪。
import os
os.environ['LANGSMITH_TRACING'] = 'false'
os.environ['LANGCHAIN_TRACING_V2'] = 'false'
os.environ['LANGCHAIN_TRACING'] = 'false'
