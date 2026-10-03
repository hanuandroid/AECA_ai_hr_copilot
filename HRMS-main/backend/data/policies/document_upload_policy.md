<!--
DEMO / SEED DATA - synthetic HR policy for the CB Nest learning environment. Not a real company policy.
Sources of truth (must stay consistent): backend/app/api/v1/endpoints/hr_policies.py (upload: MANAGER/ADMIN,
.txt/.md/.pdf, 2 MB, UTF-8 text) and employees.py (employee documents: APPOINTMENT/PAYSLIP/TAX/OTHER,
.pdf/.txt/.md/.doc/.docx, 5 MB, only OTHER deletable, password-protected payslips).
-->
# Document Upload Policy

## HR Policy Documents

Only managers and HR administrators may upload HR policy documents to the HR Policies library.

Accepted policy file types are .txt, .md and .pdf. The maximum file size is 2 MB, and .txt and .md files must be UTF-8 text.

## Employee Documents

Employees can upload their own documents from the My Documents page. Each document must have a type: Appointment, Payslip, Tax or Other.

Accepted employee document file types are .pdf, .txt, .md, .doc and .docx. The maximum file size is 5 MB.

Managers and HR administrators may upload documents on behalf of an employee.

## Deleting Documents

Employees may delete only their own documents of type Other. Appointment, Payslip and Tax documents cannot be deleted by employees.

## Payslip Protection

Payslip PDFs are password protected. The password is your date of birth in DD-MM-YY format.
