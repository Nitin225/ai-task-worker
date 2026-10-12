| # | Task | Result | Steps | Questions asked | Approvals | Time (s) | Model |
|---|------|--------|-------|-----------------|-----------|----------|-------|
| 1 | Normal entry | PASS | 14 | 0 | 2 | 151 | groq:qwen/qwen3.8-27b |
| 2 | Create: first submit fails with 500 | PASS | 15 | 0 | 2 | 191 | groq:qwen/qwen3.8-27b |
| 3 | Ambiguous: two invoices tie for latest | PASS | 15 | 1 | 1 | 188 | groq:qwen/qwen3.8-27b |
| 4 | Missing info: due date is 'Net 30' | PASS | 16 | 1 | 1 | 231 | groq:qwen/qwen3.8-27b |
| 5 | Idempotency: record already exists | PASS | 7 | 0 | 0 | 80 | groq:qwen/qwen3.8-27b |
| 6 | Update: change an invoice amount | PASS | 8 | 0 | 1 | 65 | groq:qwen/qwen3.8-27b |
| 7 | Update: change a vendor's email (other section) | PASS | 9 | 0 | 1 | 77 | groq:qwen/qwen3.8-27b |
| 8 | Update: mark an invoice as Paid | PASS | 8 | 0 | 1 | 62 | groq:qwen/qwen3.8-27b |
| 9 | Read-only question: total of Pending invoices | PASS | 6 | 0 | 0 | 43 | groq:qwen/qwen3.8-27b |

9/9 tasks passed (1 run per task).