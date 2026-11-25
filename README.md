# Payroll Management System — Full-Stack Python

Built as a Software Engineering Lab web application using Python, Flask, SQLite, HTML, CSS and Bootstrap.

## Run on Windows / VS Code

1. Extract the ZIP and open `Payroll_FullStack_Python` in VS Code.
2. Open Terminal and run:

   ```bash
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   python app.py
   ```

3. Open `http://127.0.0.1:5000` in your browser.

## Demo accounts

| Role | Username | Password |
|---|---|---|
| Admin | `admin` | `admin123` |
| HR | `hr` | `hr123` |
| Manager | `manager` | `manager123` |
| Employee | `aarav` | `emp123` |
| Employee | `diya` | `emp123` |
| Employee | `rohan` | `emp123` |

Change demo passwords after login. Newly created employee records receive a login with username equal to the email prefix and temporary password `emp123`.

## Role responsibilities and permissions

### Employee
- Login securely and update own profile/password.
- Apply for leave and view leave progress, review outcomes and rejection reasons.
- View own payslips and notifications.

### Manager
- View employee directory and operational information in read-only mode.
- Perform the first-stage leave review.
- Approve a leave request to forward it to HR, or reject it with a mandatory reason.
- Cannot perform HR final approval or change employee compensation.

### HR
- Create, edit and deactivate employee records.
- Maintain attendance records.
- Manage salary structures and compensation components.
- Perform the final leave decision after manager approval.
- Generate monthly payroll and HR reports.
- View audit history, but cannot manage system user accounts or system configuration.

### Admin
- Create user accounts and assign Admin, HR, Manager or Employee roles.
- Activate/deactivate user access.
- Manage department configuration.
- View employee, attendance, salary and payroll information in read-only mode.
- Access operational reports, audit logs and database backup.
- Does not edit employee compensation or make HR leave decisions.

## Leave approval workflow

1. Employee submits a leave request with type, dates and reason.
2. The request enters **Pending Manager Approval**.
3. Manager approves or rejects. Rejection requires a reason of at least five characters.
4. If approved by the manager, the request moves to **Pending HR Approval** and HR is notified.
5. HR approves or rejects the request as the final decision. Rejection also requires a reason of at least five characters.
6. The employee receives a notification. Both stage decisions and rejection reasons are visible in the leave history and leave report.

A manager rejection ends the workflow; HR cannot review it. HR cannot review a request until the manager approves it.

## Modules

Authentication and role-based access; employee records; manual attendance; two-stage leave workflow; salary structures; attendance-adjusted payroll; payslips; reports and CSV export; profile/password management; notifications; audit log; user access management; department configuration; SQLite backup.

## Payroll calculation rules

- Working days: Monday to Friday in the selected month.
- Unpaid days: recorded Absents + 0.5 for each Half-day + approved Unpaid leave days.
- Payable days = working days - unpaid days (minimum 0).
- Earnings are prorated by payable days / working days.
- Gross = Basic + HRA + Allowance + Bonus.
- Net = Gross - PF - Other deductions.

The calendar assumes Monday-Friday working days and does not include public holidays. Do not record both an Absent attendance day and an Unpaid leave day for the same date, because both would currently be counted. Paid/Sick/Casual leave does not reduce payable days.

## Existing database upgrade

The application automatically adds the new manager/HR leave-review columns to an existing SQLite database when it starts. Existing employee, payroll and attendance records are preserved. Back up `instance/payroll.db` before upgrading as a general precaution.

## Scope and security

Advanced income-tax calculation, bank transfer, biometric attendance, mobile app, AI prediction and external HR integrations are not implemented. Passwords are hashed, routes use role checks, forms use CSRF tokens, and key actions are logged. This is an academic prototype, not a production payroll platform. For deployment, set a strong `SECRET_KEY`, use HTTPS and a production WSGI server, and establish regular backups.
