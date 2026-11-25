# Database entities

- `users`: username, password hash, role, status and optional employee link. Roles: `admin`, `hr`, `management` (displayed as Manager), and `employee`.
- `departments`: department names used by employee records; Admin maintains department configuration.
- `employees`: employee code, name, contact, department, designation and joining date.
- `attendance`: employee/date/status/check-in/check-out and remarks.
- `leaves`: leave type, date range, duration, employee reason, overall status, manager review status/reviewer/time/rejection reason, and HR review status/reviewer/time/rejection reason.
- `salary`: basic, HRA, allowance, bonus, PF, other deductions and effective date.
- `payroll`: monthly payroll snapshot with earnings, deductions and net salary.
- `payslips`: payslip issue date and payroll link.
- `notifications`: user alerts for leave workflow and other events.
- `audit`: user actions and timestamps.

## Leave status values

- `Pending Manager Approval`: awaiting first-stage review.
- `Pending HR Approval`: manager approved; awaiting HR's final decision.
- `Approved`: HR approved the request.
- `Rejected`: manager or HR rejected the request.

The manager and HR review columns preserve each stage's separate decision and rejection reason. `reviewed_by` remains as a compatibility field for the final HR reviewer.

## Relationships

Department 1—many Employees; Employee 1—many Attendance and Leave rows; Employee 1—1 current Salary; Employee 1—many Payroll; Payroll 1—1 Payslip; User 1—many Notifications and Audit entries. Leave reviewer fields reference the Users table.

The application performs a safe schema upgrade when it starts, adding the new leave-review columns to earlier project databases without deleting existing records.
