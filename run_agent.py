import sys
from agent.loop import run_agent

task = " ".join(sys.argv[1:]) or "List the files in data/invoices and tell me what you see."
print("TASK:", task)
print("\nFINAL:", run_agent(task))