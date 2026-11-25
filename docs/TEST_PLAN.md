# Test Plan — Payroll Management System

Fill Actual Result and Pass/Fail only after executing the tests in the local environment.

| ID | Test | Expected result |
|---|---|---|
| TC-01 | Login with valid credentials | Correct role dashboard opens |
| TC-02 | Invalid login | Access denied |
| TC-03 | Employee opens Admin user-management route | HTTP 403 |
| TC-04 | HR adds a valid employee | Employee record and login are created |
| TC-05 | Duplicate employee code/email | Validation message; no duplicate row |
| TC-06 | HR edits employee | Changes saved |
| TC-07 | HR deactivates employee | Employee login disabled; historical data retained |
| TC-08 | HR saves attendance | Attendance row appears |
| TC-09 | Duplicate employee/date attendance | Existing record updated |
| TC-10 | Employee submits leave | Status becomes Pending Manager Approval |
| TC-11 | Manager opens leave queue | Manager can see first-stage pending requests |
| TC-12 | Manager approves leave | Status becomes Pending HR Approval; HR is notified |
| TC-13 | Manager rejects without reason | Rejection is blocked |
| TC-14 | Manager rejects with reason | Final status becomes Rejected and reason is stored |
| TC-15 | Manager attempts HR final approval | HTTP 403 |
| TC-16 | HR tries to review before manager approval | Review is blocked |
| TC-17 | HR approves manager-approved request | Final status becomes Approved; employee is notified |
| TC-18 | HR rejects manager-approved request without reason | Rejection is blocked |
| TC-19 | HR rejects with reason | Final status becomes Rejected and reason is stored |
| TC-20 | Admin opens employee edit or salary edit route | HTTP 403; read-only pages remain available |
| TC-21 | HR opens Admin user-management route | HTTP 403 |
| TC-22 | Admin creates user account | Account created with selected role |
| TC-23 | Admin deactivates another user | Account status becomes Inactive |
| TC-24 | Admin adds duplicate department | Duplicate prevented with message |
| TC-25 | HR saves valid salary structure | Components saved |
| TC-26 | Negative salary component | Validation error |
| TC-27 | HR generates payroll | Payroll and payslip created |
| TC-28 | Generate same month twice | Duplicate entries skipped |
| TC-29 | Absent / half-day / approved unpaid leave | Payable days and earnings prorated |
| TC-30 | Employee opens own payslip | Payslip displayed |
| TC-31 | Employee opens another employee payslip | HTTP 403 |
| TC-32 | Export leave report | CSV includes manager/HR statuses and rejection reasons |
| TC-33 | Admin downloads database backup | SQLite backup downloads |
| TC-34 | Key action performed | Audit log entry created |
