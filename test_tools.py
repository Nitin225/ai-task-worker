from agent.tools import Browser, list_files, read_file

print(list_files("data/invoices"))
print(read_file("data/invoices/acme_2026-04.txt"))

b = Browser()
print(b.open("http://localhost:8000/login"))
print(b.type(1, "admin"))
print(b.type(2, "admin123"))
print(b.click(3))
input("press enter to close...")
b.close()