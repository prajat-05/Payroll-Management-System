# Requirement Traceability

| Requirement | Implemented module |
|---|---|
| Secure login and role access | Login, password hashing and role decorators |
| Employee records | HR-managed employee directory; Admin and Manager read-only access |
| Attendance | HR-managed manual attendance; Admin and Manager read-only access |
| Employee leave application | Employee leave form and notifications |
| Manager first-stage leave approval | Manager leave queue; approve forwards to HR; reject requires a reason |
| HR final leave approval | HR queue only displays manager-approved requests; final approve/reject requires a reason for rejection |
| Leave rejection reason | Separate manager and HR rejection reasons displayed in leave history and reports |
| HR compensation management | Salary structures and compensation components |
| Monthly payroll | HR generates attendance/unpaid-leave adjusted payroll; Admin can view operational results |
| Payslips | Printable employee payslips; HR/Admin access to all records |
| Reports | HR/Admin employee, attendance, leave, salary and payroll reports; CSV export |
| Profile/password | Account and password pages |
| Notifications | In-app notifications at manager and HR workflow stages |
| HR/Admin separation | HR manages employee/compensation operations; Admin manages user accounts, departments, audit and backup |
| User access | Admin creates accounts and activates/deactivates users |
| System configuration | Admin maintains department configuration |
| Audit and backup | Admin audit-log management and database backup; HR read-only audit access |

Employee deletion is implemented as deactivation to preserve payroll history. Advanced income-tax calculation, bank transfer, biometric attendance, mobile app, AI prediction and external HR integrations remain outside the academic project scope.
