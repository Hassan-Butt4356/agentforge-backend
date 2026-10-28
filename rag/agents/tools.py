"""
Extension point for additional ReActAgent tools.

To add a tool:
  1. Define or import it here.
  2. Import it in base_agent.py and append to the `tools` list.

Examples:
  - Calculator: uncomment below
  - Web search: pip install llama-index-tools-tavily-research
  - Code interpreter: pip install llama-index-tools-code-interpreter
"""

# import math
# from llama_index.core.tools import FunctionTool
#
# def calculator(expression: str) -> str:
#     try:
#         return str(eval(expression, {"__builtins__": {}}, {"math": math}))
#     except Exception as e:
#         return f"Error: {e}"
#
# calculator_tool = FunctionTool.from_defaults(
#     fn=calculator,
#     name="calculator",
#     description="Evaluate math expressions. Input: '2 + 2' or 'math.sqrt(16)'.",
# )
