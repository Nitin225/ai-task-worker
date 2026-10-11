from agent.tools import read_file
from agent.verifier import verify

path = "data/invoices/acme_2026-04.txt"
evidence = [(path, read_file(path))]
task = "Find the latest invoice from Acme Supplies and enter it into the ERP."
print(verify(task, "Done, I saved the invoice.", evidence))