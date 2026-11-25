import os, sqlite3, secrets, io, csv
from datetime import date
from calendar import monthrange
from functools import wraps
from contextlib import contextmanager
from flask import Flask, request, redirect, url_for, session, flash, abort, render_template_string, make_response, send_file
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "instance", "payroll.db")
os.makedirs(os.path.dirname(DB), exist_ok=True)
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-development-secret")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS departments(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS employees(
 id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, first_name TEXT NOT NULL, last_name TEXT NOT NULL,
 email TEXT UNIQUE NOT NULL, phone TEXT DEFAULT '', department_id INTEGER, designation TEXT NOT NULL,
 joining_date TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Active',
 FOREIGN KEY(department_id) REFERENCES departments(id));
CREATE TABLE IF NOT EXISTS users(
 id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('admin','hr','employee','management')),
 employee_id INTEGER UNIQUE, status TEXT DEFAULT 'Active',
 FOREIGN KEY(employee_id) REFERENCES employees(id));
CREATE TABLE IF NOT EXISTS attendance(
 id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL, work_date TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('Present','Absent','Half-day','Weekly Off')),
 check_in TEXT DEFAULT '', check_out TEXT DEFAULT '', remarks TEXT DEFAULT '',
 UNIQUE(employee_id,work_date), FOREIGN KEY(employee_id) REFERENCES employees(id));
CREATE TABLE IF NOT EXISTS leaves(
 id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL, leave_type TEXT NOT NULL,
 start_date TEXT NOT NULL, end_date TEXT NOT NULL, days REAL NOT NULL, reason TEXT NOT NULL,
 status TEXT DEFAULT 'Pending Manager Approval', reviewed_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 manager_status TEXT DEFAULT 'Pending', manager_reviewed_by INTEGER, manager_reviewed_at TEXT, manager_rejection_reason TEXT DEFAULT '',
 hr_status TEXT DEFAULT 'Pending', hr_reviewed_by INTEGER, hr_reviewed_at TEXT, hr_rejection_reason TEXT DEFAULT '',
 FOREIGN KEY(employee_id) REFERENCES employees(id), FOREIGN KEY(reviewed_by) REFERENCES users(id),
 FOREIGN KEY(manager_reviewed_by) REFERENCES users(id), FOREIGN KEY(hr_reviewed_by) REFERENCES users(id));
CREATE TABLE IF NOT EXISTS salary(
 id INTEGER PRIMARY KEY, employee_id INTEGER UNIQUE NOT NULL, basic REAL DEFAULT 0, hra REAL DEFAULT 0,
 allowance REAL DEFAULT 0, bonus REAL DEFAULT 0, pf REAL DEFAULT 0, other_deduction REAL DEFAULT 0,
 effective_from TEXT NOT NULL, FOREIGN KEY(employee_id) REFERENCES employees(id));
CREATE TABLE IF NOT EXISTS payroll(
 id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL, pay_month TEXT NOT NULL,
 working_days INTEGER, payable_days REAL, basic REAL, hra REAL, allowance REAL, bonus REAL,
 gross REAL, pf REAL, other_deduction REAL, absence_deduction REAL, total_deduction REAL,
 net REAL, status TEXT DEFAULT 'Generated', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(employee_id,pay_month), FOREIGN KEY(employee_id) REFERENCES employees(id));
CREATE TABLE IF NOT EXISTS payslips(
 id INTEGER PRIMARY KEY, payroll_id INTEGER UNIQUE NOT NULL, issue_date TEXT NOT NULL,
 payment_mode TEXT DEFAULT 'Not specified', FOREIGN KEY(payroll_id) REFERENCES payroll(id));
CREATE TABLE IF NOT EXISTS notifications(
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, message TEXT NOT NULL,
 is_read INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(user_id) REFERENCES users(id));
CREATE TABLE IF NOT EXISTS audit(
 id INTEGER PRIMARY KEY, user_id INTEGER, action TEXT NOT NULL, details TEXT DEFAULT '',
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(user_id) REFERENCES users(id));
"""

@contextmanager
def conn():
    """Open a short-lived SQLite connection and always close it."""
    c = sqlite3.connect(DB, timeout=20)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=20000")
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

def init_db():
    with conn() as c:
        # WAL allows readers and a writer to work concurrently more reliably.
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.executescript(SCHEMA)
        # Upgrade databases created by earlier versions without deleting existing records.
        existing = {r[1] for r in c.execute("PRAGMA table_info(leaves)").fetchall()}
        migrations = {
            "manager_status": "TEXT DEFAULT 'Pending'",
            "manager_reviewed_by": "INTEGER",
            "manager_reviewed_at": "TEXT",
            "manager_rejection_reason": "TEXT DEFAULT ''",
            "hr_status": "TEXT DEFAULT 'Pending'",
            "hr_reviewed_by": "INTEGER",
            "hr_reviewed_at": "TEXT",
            "hr_rejection_reason": "TEXT DEFAULT ''",
        }
        for column, definition in migrations.items():
            if column not in existing:
                c.execute(f"ALTER TABLE leaves ADD COLUMN {column} {definition}")
        c.execute("UPDATE leaves SET status='Pending Manager Approval' WHERE status='Pending'")
        c.execute("UPDATE leaves SET manager_status='Approved', hr_status='Approved' WHERE status='Approved' AND manager_status='Pending'")
        c.execute("UPDATE leaves SET manager_status='Rejected' WHERE status='Rejected' AND manager_status='Pending'")
        if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            for d in ["Engineering","Human Resources","Finance"]:
                c.execute("INSERT INTO departments(name) VALUES(?)",(d,))
            dep={r["name"]:r["id"] for r in c.execute("SELECT * FROM departments")}
            samples=[
              ("EMP001","Aarav","Sharma","aarav@example.com","9000000001",dep["Engineering"],"Software Developer","2025-01-10"),
              ("EMP002","Diya","Das","diya@example.com","9000000002",dep["Human Resources"],"HR Executive","2025-03-15"),
              ("EMP003","Rohan","Sen","rohan@example.com","9000000003",dep["Finance"],"Accountant","2024-11-01")]
            for e in samples:
                cur=c.execute("""INSERT INTO employees(code,first_name,last_name,email,phone,department_id,designation,joining_date)
                                 VALUES(?,?,?,?,?,?,?,?)""",e)
                eid=cur.lastrowid
                c.execute("INSERT INTO users(username,password_hash,role,employee_id) VALUES(?,?,?,?)",
                          (e[3].split("@")[0],generate_password_hash("emp123"),"employee",eid))
                c.execute("""INSERT INTO salary(employee_id,basic,hra,allowance,bonus,pf,other_deduction,effective_from)
                              VALUES(?,?,?,?,?,?,?,?)""",(eid,30000 if eid==1 else 25000,8000,3000,1000,1800,500,"2025-01-01"))
            c.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",("admin",generate_password_hash("admin123"),"admin"))
            c.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",("hr",generate_password_hash("hr123"),"hr"))
            c.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",("manager",generate_password_hash("manager123"),"management"))

def log(action, detail=""):
    if session.get("uid"):
        with conn() as c: c.execute("INSERT INTO audit(user_id,action,details) VALUES(?,?,?)",(session["uid"],action,detail[:300]))
def notice(uid,msg):
    if uid:
        with conn() as c: c.execute("INSERT INTO notifications(user_id,message) VALUES(?,?)",(uid,msg))

def iso(s):
    try: date.fromisoformat(s); return True
    except: return False

@app.before_request
def csrf():
    if "csrf" not in session: session["csrf"]=secrets.token_hex(24)
    if request.method=="POST" and not secrets.compare_digest(request.form.get("csrf",""),session.get("csrf","")):
        abort(400)

@app.context_processor
def globals():
    unread=0
    if session.get("uid"):
        with conn() as c: unread=c.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0",(session["uid"],)).fetchone()[0]
    return {"csrf":session.get("csrf"),"unread":unread,"today":date.today().isoformat()}

def login_required(f):
    @wraps(f)
    def w(*a,**k):
        if not session.get("uid"): return redirect(url_for("login"))
        return f(*a,**k)
    return w
def roles(*allowed):
    def deco(f):
        @wraps(f)
        @login_required
        def w(*a,**k):
            if session.get("role") not in allowed: abort(403)
            return f(*a,**k)
        return w
    return deco
def admin(): return session.get("role") == "hr"
def is_admin(): return session.get("role") == "admin"
def page(title,body,**ctx):
    return render_template_string(BASE_HTML, title=title, body=render_template_string(body,**ctx))

@app.route("/")
def home(): return redirect(url_for("dashboard") if session.get("uid") else url_for("login"))

@app.route("/login",methods=["GET","POST"])
def login():
    if request.method=="POST":
        with conn() as c: u=c.execute("SELECT * FROM users WHERE username=? AND status='Active'",(request.form.get("username","").strip(),)).fetchone()
        if u and check_password_hash(u["password_hash"],request.form.get("password","")):
            session.clear(); session.update(uid=u["id"],username=u["username"],role=u["role"],eid=u["employee_id"],csrf=secrets.token_hex(24))
            log("LOGIN","successful"); return redirect(url_for("dashboard"))
        flash("Invalid username or password.")
    return page("Login",LOGIN)

@app.post("/logout")
@login_required
def logout():
    log("LOGOUT"); session.clear(); return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    with conn() as c:
        if session["role"]=="employee":
            e=c.execute("SELECT e.*,d.name department FROM employees e LEFT JOIN departments d ON d.id=e.department_id WHERE e.id=?",(session["eid"],)).fetchone()
            p=c.execute("SELECT * FROM payroll WHERE employee_id=? ORDER BY pay_month DESC LIMIT 1",(session["eid"],)).fetchone()
            ls=c.execute("SELECT * FROM leaves WHERE employee_id=? ORDER BY id DESC LIMIT 5",(session["eid"],)).fetchall()
            return page("Employee Dashboard",EMP_DASH,emp=e,p=p,leaves=ls)
        counts=[c.execute("SELECT COUNT(*) FROM employees WHERE status='Active'").fetchone()[0],
                c.execute("SELECT COUNT(*) FROM leaves WHERE status IN ('Pending Manager Approval','Pending HR Approval')").fetchone()[0],
                c.execute("SELECT COUNT(*) FROM payroll").fetchone()[0],
                c.execute("SELECT COALESCE(SUM(net),0) FROM payroll WHERE pay_month=?",(date.today().strftime("%Y-%m"),)).fetchone()[0]]
        recent=c.execute("""SELECT p.*,e.code,e.first_name,e.last_name FROM payroll p JOIN employees e ON e.id=p.employee_id ORDER BY p.id DESC LIMIT 6""").fetchall()
    return page("Dashboard",ADMIN_DASH,counts=counts,recent=recent)

@app.route("/employees",methods=["GET","POST"])
@roles("admin","hr","management")
def employees():
    q=request.args.get("q","")
    with conn() as c:
        rows=c.execute("""SELECT e.*,d.name department FROM employees e LEFT JOIN departments d ON d.id=e.department_id
          WHERE e.code LIKE ? OR e.first_name LIKE ? OR e.last_name LIKE ? OR e.email LIKE ? ORDER BY e.id DESC""",
          tuple([f"%{q}%"]*4)).fetchall()
        deps=c.execute("SELECT * FROM departments ORDER BY name").fetchall()
    return page("Employees",EMPLOYEES,rows=rows,q=q,editable=admin(),deps=deps)

@app.route("/employees/add",methods=["GET","POST"])
@roles("hr")
def employee_add():
    with conn() as c: deps=c.execute("SELECT * FROM departments ORDER BY name").fetchall()
    if request.method=="POST":
        f=request.form
        try:
            required=["code","first_name","last_name","email","designation","joining_date"]
            if not all(f.get(x,"").strip() for x in required) or not iso(f["joining_date"]) or "@" not in f["email"]: raise ValueError()
            with conn() as c:
                cur=c.execute("""INSERT INTO employees(code,first_name,last_name,email,phone,department_id,designation,joining_date)
                  VALUES(?,?,?,?,?,?,?,?)""",(f["code"].strip(),f["first_name"].strip(),f["last_name"].strip(),f["email"].strip().lower(),f.get("phone",""),int(f["department_id"]),f["designation"].strip(),f["joining_date"]))
                eid=cur.lastrowid; uname=f["email"].split("@")[0].lower()
                c.execute("INSERT INTO users(username,password_hash,role,employee_id) VALUES(?,?,?,?)",(uname,generate_password_hash("emp123"),"employee",eid))
                c.execute("INSERT INTO salary(employee_id,effective_from) VALUES(?,?)",(eid,f["joining_date"]))
            log("EMPLOYEE_ADD",f["code"]); flash(f"Employee added. Username: {uname}; temporary password: emp123.")
            return redirect(url_for("employees"))
        except (ValueError,sqlite3.IntegrityError): flash("Invalid details or duplicate employee code/email/username.")
    return page("Add Employee",EMP_FORM,emp=None,deps=deps)

@app.route("/employees/<int:eid>/edit",methods=["GET","POST"])
@roles("hr")
def employee_edit(eid):
    with conn() as c:
        e=c.execute("SELECT * FROM employees WHERE id=?",(eid,)).fetchone()
        deps=c.execute("SELECT * FROM departments ORDER BY name").fetchall()
    if not e: abort(404)
    if request.method=="POST":
        f=request.form
        try:
            if not all(f.get(x,"").strip() for x in ["code","first_name","last_name","email","designation","joining_date"]) or not iso(f["joining_date"]): raise ValueError()
            with conn() as c:
                c.execute("""UPDATE employees SET code=?,first_name=?,last_name=?,email=?,phone=?,department_id=?,designation=?,joining_date=?,status=? WHERE id=?""",
                 (f["code"],f["first_name"],f["last_name"],f["email"],f.get("phone",""),int(f["department_id"]),f["designation"],f["joining_date"],f["status"],eid))
            log("EMPLOYEE_EDIT",e["code"]); flash("Employee updated."); return redirect(url_for("employees"))
        except (ValueError,sqlite3.IntegrityError): flash("Invalid data or duplicate employee code/email.")
    return page("Edit Employee",EMP_FORM,emp=e,deps=deps)

@app.post("/employees/<int:eid>/deactivate")
@roles("hr")
def employee_deactivate(eid):
    with conn() as c:
        c.execute("UPDATE employees SET status='Inactive' WHERE id=?",(eid,))
        c.execute("UPDATE users SET status='Inactive' WHERE employee_id=?",(eid,))
    log("EMPLOYEE_DEACTIVATE",str(eid)); flash("Employee deactivated; historical payroll is retained.")
    return redirect(url_for("employees"))

@app.route("/attendance",methods=["GET","POST"])
@roles("admin","hr","management")
def attendance():
    month=request.args.get("month",date.today().strftime("%Y-%m"))
    with conn() as c:
        emps=c.execute("SELECT id,code,first_name,last_name FROM employees WHERE status='Active' ORDER BY first_name").fetchall()
        rows=c.execute("""SELECT a.*,e.code,e.first_name,e.last_name FROM attendance a JOIN employees e ON e.id=a.employee_id
           WHERE substr(a.work_date,1,7)=? ORDER BY a.work_date DESC""",(month,)).fetchall()
    return page("Attendance",ATTENDANCE,emps=emps,rows=rows,month=month,editable=admin())

@app.post("/attendance/save")
@roles("hr")
def attendance_save():
    f=request.form; d=f.get("work_date",""); st=f.get("status","")
    if not iso(d) or st not in ("Present","Absent","Half-day","Weekly Off"): flash("Invalid attendance details.")
    else:
        try:
            with conn() as c:
                c.execute("""INSERT INTO attendance(employee_id,work_date,status,check_in,check_out,remarks)
                  VALUES(?,?,?,?,?,?) ON CONFLICT(employee_id,work_date) DO UPDATE SET status=excluded.status,
                  check_in=excluded.check_in,check_out=excluded.check_out,remarks=excluded.remarks""",
                  (int(f["employee_id"]),d,st,f.get("check_in",""),f.get("check_out",""),f.get("remarks","")))
            log("ATTENDANCE_SAVE",d); flash("Attendance saved.")
        except: flash("Please select a valid employee.")
    return redirect(url_for("attendance",month=d[:7]))

@app.route("/leaves")
@login_required
def leaves():
    with conn() as c:
        if session["role"]=="employee":
            rows=c.execute("""SELECT l.*,e.code,e.first_name,e.last_name FROM leaves l JOIN employees e ON e.id=l.employee_id
              WHERE l.employee_id=? ORDER BY l.id DESC""",(session["eid"],)).fetchall()
        elif session["role"] in ("admin","hr","management"):
            rows=c.execute("""SELECT l.*,e.code,e.first_name,e.last_name FROM leaves l JOIN employees e ON e.id=l.employee_id ORDER BY l.id DESC""").fetchall()
        else: abort(403)
    return page("Leave Requests",LEAVES,rows=rows,review=session["role"] in ("hr", "management"), stage=("manager" if session["role"]=="management" else "hr"))

@app.route("/leaves/apply",methods=["GET","POST"])
@roles("employee")
def leave_apply():
    if request.method=="POST":
        f=request.form; start=f.get("start_date",""); end=f.get("end_date",""); reason=f.get("reason","").strip(); typ=f.get("leave_type","")
        if not iso(start) or not iso(end) or end<start or not reason or typ not in ("Paid","Unpaid","Sick","Casual"):
            flash("Enter valid dates, type and reason.")
        else:
            days=(date.fromisoformat(end)-date.fromisoformat(start)).days+1
            with conn() as c:
                c.execute("INSERT INTO leaves(employee_id,leave_type,start_date,end_date,days,reason,status,manager_status,hr_status) VALUES(?,?,?,?,?,?,'Pending Manager Approval','Pending','Pending')",(session["eid"],typ,start,end,days,reason))
                managers=c.execute("SELECT id FROM users WHERE role='management' AND status='Active'").fetchall()
                for u in managers:
                    c.execute("INSERT INTO notifications(user_id,message) VALUES(?,?)",(u["id"],f"New leave request from {session['username']} requires your review."))
            log("LEAVE_APPLY",f"{start} to {end}"); flash("Leave request submitted to your manager for first-stage approval.")
            return redirect(url_for("leaves"))
    return page("Apply Leave",LEAVE_FORM)

@app.post("/leaves/<int:lid>/<stage>/<action>")
@login_required
def leave_review(lid,stage,action):
    role=session.get("role")
    if action not in ("approve","reject") or stage not in ("manager","hr"):
        abort(400)
    if (stage=="manager" and role!="management") or (stage=="hr" and role!="hr"):
        abort(403)
    rejection_reason=request.form.get("rejection_reason","").strip()
    if action=="reject" and len(rejection_reason)<5:
        flash("A rejection reason of at least 5 characters is required.")
        return redirect(url_for("leaves"))
    status=None
    with conn() as c:
        leave=c.execute("SELECT * FROM leaves WHERE id=?",(lid,)).fetchone()
        if not leave: abort(404)
        if stage=="manager":
            if leave["status"]!="Pending Manager Approval":
                flash("This request is no longer awaiting manager review.")
            else:
                stage_status="Approved" if action=="approve" else "Rejected"
                overall="Pending HR Approval" if action=="approve" else "Rejected"
                c.execute("UPDATE leaves SET manager_status=?,manager_reviewed_by=?,manager_reviewed_at=CURRENT_TIMESTAMP,manager_rejection_reason=?,status=? WHERE id=?",
                          (stage_status,session["uid"],rejection_reason if action=="reject" else "",overall,lid))
                if action=="approve":
                    hr_users=c.execute("SELECT id FROM users WHERE role='hr' AND status='Active'").fetchall()
                    for u in hr_users:
                        c.execute("INSERT INTO notifications(user_id,message) VALUES(?,?)",(u["id"],f"Leave request #{lid} has manager approval and needs your final review."))
                status=overall
        else:
            if leave["status"]!="Pending HR Approval" or leave["manager_status"]!="Approved":
                flash("HR can review only requests approved by the manager.")
            else:
                stage_status="Approved" if action=="approve" else "Rejected"
                c.execute("UPDATE leaves SET hr_status=?,hr_reviewed_by=?,hr_reviewed_at=CURRENT_TIMESTAMP,hr_rejection_reason=?,reviewed_by=?,status=? WHERE id=?",
                          (stage_status,session["uid"],rejection_reason if action=="reject" else "",session["uid"],stage_status,lid))
                status=stage_status
        if status:
            employee_user=c.execute("SELECT id FROM users WHERE employee_id=?",(leave["employee_id"],)).fetchone()
            if employee_user:
                msg=(f"Your leave request #{lid} was rejected by the manager. Reason: {rejection_reason}" if stage=="manager" and action=="reject" else
                     f"Your leave request #{lid} is awaiting HR final review." if stage=="manager" else
                     f"Your leave request #{lid} was {status.lower()} by HR." + (f" Reason: {rejection_reason}" if action=="reject" else ""))
                c.execute("INSERT INTO notifications(user_id,message) VALUES(?,?)",(employee_user["id"],msg))
    if status:
        log(f"LEAVE_{stage.upper()}_{action.upper()}",f"Leave #{lid}; {rejection_reason}" if action=="reject" else f"Leave #{lid}")
        flash("Manager review recorded." if stage=="manager" else "HR final decision recorded.")
    return redirect(url_for("leaves"))

@app.route("/salary")
@roles("admin","hr")
def salary():
    with conn() as c:
        rows=c.execute("""SELECT s.*,e.code,e.first_name,e.last_name FROM salary s JOIN employees e ON e.id=s.employee_id ORDER BY e.code""").fetchall()
    return page("Salary Structure",SALARY,rows=rows,editable=admin())

@app.route("/salary/<int:eid>",methods=["GET","POST"])
@roles("hr")
def salary_edit(eid):
    with conn() as c:
        e=c.execute("SELECT * FROM employees WHERE id=?",(eid,)).fetchone()
        s=c.execute("SELECT * FROM salary WHERE employee_id=?",(eid,)).fetchone()
    if not e: abort(404)
    if request.method=="POST":
        f=request.form
        try:
            fields=["basic","hra","allowance","bonus","pf","other_deduction"]
            vals=[round(float(f[x]),2) for x in fields]
            if min(vals)<0 or not iso(f["effective_from"]): raise ValueError()
            with conn() as c:
                c.execute("""INSERT INTO salary(employee_id,basic,hra,allowance,bonus,pf,other_deduction,effective_from)
                  VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(employee_id) DO UPDATE SET basic=excluded.basic,hra=excluded.hra,
                  allowance=excluded.allowance,bonus=excluded.bonus,pf=excluded.pf,other_deduction=excluded.other_deduction,
                  effective_from=excluded.effective_from""",(eid,*vals,f["effective_from"]))
            log("SALARY_UPDATE",e["code"]); flash("Salary structure saved."); return redirect(url_for("salary"))
        except: flash("All components must be non-negative numbers and effective date valid.")
    return page("Edit Salary",SALARY_FORM,emp=e,s=s)

def payroll_values(eid,period):
    y,m=map(int,period.split("-")); total=monthrange(y,m)[1]
    working=sum(1 for day in range(1,total+1) if date(y,m,day).weekday()<5)
    start=f"{period}-01"; end=f"{period}-{total:02d}"
    with conn() as c:
        s=c.execute("SELECT * FROM salary WHERE employee_id=?",(eid,)).fetchone()
        absent=c.execute("SELECT COUNT(*) FROM attendance WHERE employee_id=? AND work_date BETWEEN ? AND ? AND status='Absent'",(eid,start,end)).fetchone()[0]
        half=c.execute("SELECT COUNT(*) FROM attendance WHERE employee_id=? AND work_date BETWEEN ? AND ? AND status='Half-day'",(eid,start,end)).fetchone()[0]
        unpaid=c.execute("""SELECT COALESCE(SUM(days),0) FROM leaves WHERE employee_id=? AND status='Approved'
           AND leave_type='Unpaid' AND NOT(end_date<? OR start_date>?)""",(eid,start,end)).fetchone()[0]
    if not s: raise ValueError("Missing salary structure")
    unpaid_days=min(working,absent+half*.5+unpaid); payable=max(0,working-unpaid_days); ratio=payable/working if working else 0
    basic=round(s["basic"]*ratio,2); hra=round(s["hra"]*ratio,2); allow=round(s["allowance"]*ratio,2); bonus=round(s["bonus"]*ratio,2)
    gross=round(basic+hra+allow+bonus,2); pf=round(s["pf"]*ratio,2); other=round(s["other_deduction"]*ratio,2)
    absence=round(s["basic"]+s["hra"]+s["allowance"]+s["bonus"]-gross,2)
    deductions=round(pf+other,2); net=round(max(0,gross-deductions),2)
    return (working,payable,basic,hra,allow,bonus,gross,pf,other,absence,deductions,net)

@app.route("/payroll",methods=["GET","POST"])
@roles("admin","hr")
def payroll():
    period=request.values.get("month",date.today().strftime("%Y-%m"))
    if request.method=="POST":
        if session.get("role") != "hr": abort(403)
        if not iso(period+"-01"): flash("Select a valid month.")
        else:
            with conn() as c:
                emps=c.execute("SELECT id FROM employees WHERE status='Active'").fetchall(); made=skipped=0
                for e in emps:
                    try:
                        vals=payroll_values(e["id"],period)
                        cur=c.execute("""INSERT INTO payroll(employee_id,pay_month,working_days,payable_days,basic,hra,allowance,bonus,
                          gross,pf,other_deduction,absence_deduction,total_deduction,net)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(e["id"],period,*vals))
                        c.execute("INSERT INTO payslips(payroll_id,issue_date) VALUES(?,?)",(cur.lastrowid,date.today().isoformat()))
                        made+=1
                    except (sqlite3.IntegrityError,ValueError): skipped+=1
            log("PAYROLL_GENERATE",f"{period}: {made} generated, {skipped} skipped")
            flash(f"Payroll generated for {made} employee(s); {skipped} skipped.")
            return redirect(url_for("payroll",month=period))
    with conn() as c:
        rows=c.execute("""SELECT p.*,e.code,e.first_name,e.last_name FROM payroll p JOIN employees e ON e.id=p.employee_id
          WHERE p.pay_month=? ORDER BY e.code""",(period,)).fetchall()
        total=c.execute("SELECT COALESCE(SUM(net),0) FROM payroll WHERE pay_month=?",(period,)).fetchone()[0]
    return page("Payroll",PAYROLL,rows=rows,period=period,total=total,editable=admin())

@app.route("/payslips")
@login_required
def payslips():
    if session["role"] not in ("employee", "admin", "hr"): abort(403)
    with conn() as c:
        if session["role"]=="employee":
            rows=c.execute("""SELECT p.*,s.id slip_id,s.issue_date,e.code,e.first_name,e.last_name FROM payroll p
              JOIN payslips s ON s.payroll_id=p.id JOIN employees e ON e.id=p.employee_id WHERE p.employee_id=? ORDER BY p.pay_month DESC""",(session["eid"],)).fetchall()
        else:
            rows=c.execute("""SELECT p.*,s.id slip_id,s.issue_date,e.code,e.first_name,e.last_name FROM payroll p
              JOIN payslips s ON s.payroll_id=p.id JOIN employees e ON e.id=p.employee_id ORDER BY p.pay_month DESC""").fetchall()
    return page("Payslips",PAYSLIPS,rows=rows)

@app.route("/payslips/<int:pid>")
@login_required
def slip(pid):
    with conn() as c:
        p=c.execute("""SELECT p.*,s.id slip_id,s.issue_date,s.payment_mode,e.code,e.first_name,e.last_name,e.email,e.designation,d.name department
          FROM payroll p JOIN payslips s ON s.payroll_id=p.id JOIN employees e ON e.id=p.employee_id
          LEFT JOIN departments d ON d.id=e.department_id WHERE p.id=?""",(pid,)).fetchone()
    if not p: abort(404)
    if session["role"]=="employee" and p["employee_id"]!=session["eid"]: abort(403)
    if session["role"] not in ("employee", "admin", "hr"): abort(403)
    return page("Payslip",SLIP,p=p)

@app.route("/reports")
@roles("admin","hr")
def reports():
    typ=request.args.get("type","payroll"); period=request.args.get("month",date.today().strftime("%Y-%m"))
    with conn() as c:
        if typ=="employees": rows=c.execute("""SELECT e.*,d.name department FROM employees e LEFT JOIN departments d ON d.id=e.department_id""").fetchall()
        elif typ=="attendance": rows=c.execute("""SELECT a.*,e.code,e.first_name,e.last_name FROM attendance a JOIN employees e ON e.id=a.employee_id WHERE substr(a.work_date,1,7)=?""",(period,)).fetchall()
        elif typ=="leaves": rows=c.execute("""SELECT l.*,e.code,e.first_name,e.last_name FROM leaves l JOIN employees e ON e.id=l.employee_id ORDER BY l.id DESC""").fetchall()
        elif typ=="salary": rows=c.execute("""SELECT s.*,e.code,e.first_name,e.last_name FROM salary s JOIN employees e ON e.id=s.employee_id""").fetchall()
        else:
            typ="payroll"; rows=c.execute("""SELECT p.*,e.code,e.first_name,e.last_name FROM payroll p JOIN employees e ON e.id=p.employee_id WHERE p.pay_month=?""",(period,)).fetchall()
    return page("Reports",REPORTS,rows=rows,typ=typ,period=period)

@app.route("/reports/export")
@roles("admin","hr")
def export():
    typ=request.args.get("type","payroll"); period=request.args.get("month",date.today().strftime("%Y-%m"))
    with conn() as c:
        if typ=="employees": rows=c.execute("SELECT code,first_name,last_name,email,designation,status,joining_date FROM employees").fetchall()
        elif typ=="attendance": rows=c.execute("""SELECT e.code,a.work_date,a.status,a.check_in,a.check_out FROM attendance a JOIN employees e ON e.id=a.employee_id WHERE substr(a.work_date,1,7)=?""",(period,)).fetchall()
        elif typ=="leaves": rows=c.execute("""SELECT e.code,l.leave_type,l.start_date,l.end_date,l.days,l.reason,l.manager_status,l.manager_rejection_reason,l.hr_status,l.hr_rejection_reason,l.status FROM leaves l JOIN employees e ON e.id=l.employee_id""").fetchall()
        elif typ=="salary": rows=c.execute("""SELECT e.code,s.basic,s.hra,s.allowance,s.bonus,s.pf,s.other_deduction FROM salary s JOIN employees e ON e.id=s.employee_id""").fetchall()
        else: typ="payroll"; rows=c.execute("""SELECT e.code,p.pay_month,p.gross,p.total_deduction,p.net,p.status FROM payroll p JOIN employees e ON e.id=p.employee_id WHERE p.pay_month=?""",(period,)).fetchall()
    out=io.StringIO(); w=csv.writer(out)
    if rows: w.writerow(rows[0].keys()); [w.writerow([r[k] for k in r.keys()]) for r in rows]
    else: w.writerow(["No records"])
    resp=make_response(out.getvalue()); resp.headers["Content-Disposition"]=f"attachment; filename={typ}_{period}.csv"; resp.headers["Content-Type"]="text/csv"
    log("REPORT_EXPORT",typ); return resp

@app.route("/profile",methods=["GET","POST"])
@login_required
def profile():
    with conn() as c: u=c.execute("SELECT * FROM users WHERE id=?",(session["uid"],)).fetchone()
    if request.method=="POST":
        name=request.form.get("username","").strip()
        if len(name)<3: flash("Username must be at least 3 characters.")
        else:
            try:
                with conn() as c: c.execute("UPDATE users SET username=? WHERE id=?",(name,session["uid"]))
                session["username"]=name; log("PROFILE_UPDATE",name); flash("Profile updated."); return redirect(url_for("profile"))
            except sqlite3.IntegrityError: flash("Username already exists.")
    return page("My Account",PROFILE,u=u)

@app.route("/password",methods=["GET","POST"])
@login_required
def password():
    if request.method=="POST":
        f=request.form
        with conn() as c: u=c.execute("SELECT * FROM users WHERE id=?",(session["uid"],)).fetchone()
        if not check_password_hash(u["password_hash"],f.get("current","")): flash("Current password incorrect.")
        elif len(f.get("new",""))<8: flash("New password must be at least 8 characters.")
        elif f.get("new")!=f.get("confirm"): flash("Passwords do not match.")
        else:
            with conn() as c: c.execute("UPDATE users SET password_hash=? WHERE id=?",(generate_password_hash(f["new"]),session["uid"]))
            log("PASSWORD_CHANGE"); flash("Password updated."); return redirect(url_for("profile"))
    return page("Change Password",PASSWORD)

@app.route("/notifications")
@login_required
def notifications():
    with conn() as c:
        rows=c.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC",(session["uid"],)).fetchall()
        c.execute("UPDATE notifications SET is_read=1 WHERE user_id=?",(session["uid"],))
    return page("Notifications",NOTICES,rows=rows)

@app.route("/audit")
@roles("admin","hr")
def audit_page():
    with conn() as c: rows=c.execute("SELECT a.*,u.username FROM audit a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 200").fetchall()
    return page("Audit Log",AUDIT,rows=rows)

@app.route("/backup")
@roles("admin")
def backup():
    log("DATABASE_BACKUP","SQLite backup downloaded")
    return send_file(DB,as_attachment=True,download_name=f"payroll_backup_{date.today().isoformat()}.db")

@app.route("/admin/users",methods=["GET","POST"])
@roles("admin")
def admin_users():
    if request.method=="POST":
        f=request.form; username=f.get("username","").strip(); password=f.get("password",""); role=f.get("role",""); employee_id=f.get("employee_id") or None
        if len(username)<3 or len(password)<8 or role not in ("admin","hr","management","employee") or (role=="employee" and not employee_id):
            flash("Use a 3+ character username, an 8+ character password, a valid role, and link employee accounts to an employee record.")
        else:
            try:
                with conn() as c:
                    c.execute("INSERT INTO users(username,password_hash,role,employee_id) VALUES(?,?,?,?)",(username,generate_password_hash(password),role,int(employee_id) if employee_id else None))
                log("USER_CREATE",f"{username} ({role})"); flash("User account created.")
            except (sqlite3.IntegrityError,ValueError): flash("Username already exists, employee already has a login, or employee selection is invalid.")
        return redirect(url_for("admin_users"))
    with conn() as c:
        users=c.execute("SELECT u.*,e.code,e.first_name,e.last_name FROM users u LEFT JOIN employees e ON e.id=u.employee_id ORDER BY u.id DESC").fetchall()
        emps=c.execute("SELECT id,code,first_name,last_name FROM employees WHERE status='Active' AND id NOT IN (SELECT employee_id FROM users WHERE employee_id IS NOT NULL) ORDER BY code").fetchall()
    return page("User Access Management",ADMIN_USERS,users=users,employees=emps)

@app.post("/admin/users/<int:uid>/status")
@roles("admin")
def admin_user_status(uid):
    if uid==session["uid"]:
        flash("You cannot deactivate your own account.")
    else:
        with conn() as c:
            u=c.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
            if not u: abort(404)
            new_status="Inactive" if u["status"]=="Active" else "Active"
            c.execute("UPDATE users SET status=? WHERE id=?",(new_status,uid))
        log("USER_STATUS",f"{u['username']} -> {new_status}"); flash(f"Account {new_status.lower()}.")
    return redirect(url_for("admin_users"))

@app.route("/admin/departments",methods=["GET","POST"])
@roles("admin")
def admin_departments():
    if request.method=="POST":
        name=request.form.get("name","").strip()
        if len(name)<2: flash("Department name must contain at least 2 characters.")
        else:
            try:
                with conn() as c: c.execute("INSERT INTO departments(name) VALUES(?)",(name,))
                log("DEPARTMENT_CREATE",name); flash("Department added.")
            except sqlite3.IntegrityError: flash("That department already exists.")
        return redirect(url_for("admin_departments"))
    with conn() as c:
        rows=c.execute("SELECT d.*,COUNT(e.id) employee_count FROM departments d LEFT JOIN employees e ON e.department_id=d.id GROUP BY d.id ORDER BY d.name").fetchall()
    return page("System Configuration",ADMIN_DEPARTMENTS,rows=rows)

@app.errorhandler(403)
def forbidden(e): return page("Forbidden",ERROR,code=403,msg="You are not authorized to access this page."),403
@app.errorhandler(404)
def notfound(e): return page("Not Found",ERROR,code=404,msg="The requested page was not found."),404
@app.errorhandler(400)
def bad(e): return page("Bad Request",ERROR,code=400,msg="Invalid form token. Refresh the page and try again."),400

BASE_HTML = r"""
<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }} · Payroll</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<style>
:root{--ink:#172033;--muted:#738096;--line:#e8edf3;--blue:#315efb;--bg:#f5f7fb}
body{background:var(--bg);color:var(--ink);font-family:Inter,system-ui,Segoe UI,sans-serif}.top{background:#151d2e;padding:14px 0}.brand{color:white;text-decoration:none;font-weight:850;letter-spacing:1.2px}.dot{color:#7994ff}.navs{display:flex;gap:5px;flex-wrap:wrap;padding:13px 0;border-bottom:1px solid var(--line)}.navs a{font-size:.84rem;color:#536078;text-decoration:none;padding:7px 10px;border-radius:8px}.navs a:hover{background:#e9efff;color:var(--blue)}.panel,.stat,.login{background:white;border:1px solid var(--line);border-radius:15px;padding:20px;box-shadow:0 5px 20px #19284608}.heading{display:flex;justify-content:space-between;align-items:end;margin:8px 0 23px;gap:15px}.heading h1{font-weight:800;letter-spacing:-1px;margin:3px 0}.muted,.heading p{color:var(--muted)}.eyebrow{font-size:.68rem;letter-spacing:1.6px;font-weight:800;color:#78869d}.stat{height:100%}.stat strong{display:block;font-size:1.65rem;margin:7px 0}.stat small{color:var(--muted)}.btn{border-radius:9px;font-weight:600}.btn-primary{background:var(--blue);border-color:var(--blue)}.form-control,.form-select{border-color:#dfe5ee;border-radius:9px;padding:9px 11px}.form-label{font-size:.84rem;font-weight:650}.table{margin:0}.table>:not(caption)>*>*{padding:12px;border-bottom-color:#edf0f5;font-size:.87rem}.table thead th{font-size:.7rem;color:#8490a4;text-transform:uppercase;letter-spacing:.6px}.title2{font-size:1.05rem;font-weight:750}.loginwrap{min-height:75vh;display:grid;place-items:center}.login{width:min(100%,450px);padding:34px}.login h1{font-size:2.5rem;font-weight:850;letter-spacing:-1.5px}.login h1 span{color:var(--blue)}.demo{background:#f7f8fb;padding:12px;border-radius:10px;font-size:.78rem;line-height:1.8}.net{background:#eff3ff;border-radius:10px;padding:17px;display:flex;justify-content:space-between;color:#2749bc;font-weight:750}.net b{font-size:1.4rem}.quick a{display:block;border:1px solid var(--line);padding:11px;border-radius:9px;margin:7px 0;text-decoration:none;color:var(--ink)}.quick a:hover{background:#f7f9ff}.formbox{max-width:850px}.alert{border-radius:10px}@media(max-width:600px){.heading{align-items:start;flex-direction:column}.panel{padding:14px}.login{padding:24px}}@media print{.top,.navs,footer,button{display:none!important}body{background:white}.panel{box-shadow:none;border:0}}
</style></head><body>
<nav class="top"><div class="container-fluid px-lg-4 d-flex justify-content-between align-items-center"><a class="brand" href="/">PAYROLL<span class="dot">.</span></a>
{% if session.get('uid') %}<div class="d-flex align-items-center gap-2"><a class="btn btn-sm btn-outline-light" href="/notifications">Alerts{% if unread %} ({{ unread }}){% endif %}</a><span class="text-white-50 small d-none d-md-inline">{{ session.username }} · {{ 'Manager' if session.role=='management' else session.role|title }}</span><form method="post" action="/logout"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn btn-sm btn-light">Logout</button></form></div>{% endif %}</div></nav>
{% if session.get('uid') %}<div class="container-fluid px-lg-4"><div class="navs"><a href="/dashboard">Overview</a>
{% if session.role in ['admin','hr','management'] %}<a href="/leaves">{% if session.role=='management' %}Manager Leave Review{% elif session.role=='hr' %}HR Leave Review{% else %}Leave Overview{% endif %}</a><a href="/employees">Employees</a><a href="/attendance">Attendance</a>{% endif %}
{% if session.role in ['admin','hr'] %}<a href="/salary">Salary</a><a href="/payroll">Payroll</a><a href="/payslips">Payslips</a><a href="/reports">Reports</a>{% endif %}
{% if session.role=='admin' %}<a href="/admin/users">User Access</a><a href="/admin/departments">System Configuration</a><a href="/audit">Audit Log</a><a href="/backup">Backup</a>{% elif session.role=='hr' %}<a href="/audit">Audit (view)</a>{% elif session.role=='employee' %}<a href="/leaves">My Leave</a><a href="/payslips">My Payslips</a>{% endif %}
<a href="/profile">Account</a></div></div>{% endif %}
<main class="container-fluid px-lg-4 py-4">{% with msgs=get_flashed_messages() %}{% for m in msgs %}<div class="alert alert-info alert-dismissible fade show">{{ m }}<button class="btn-close" data-bs-dismiss="alert"></button></div>{% endfor %}{% endwith %}{{ body|safe }}</main><footer class="container-fluid px-lg-4 pb-4 small text-secondary">Payroll Management System · Software Engineering Lab</footer>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script></body></html>
"""
LOGIN=r"""<div class="loginwrap"><div class="login"><div class="eyebrow">WORKFORCE MANAGEMENT</div><h1>Payroll made<br><span>simple.</span></h1><p class="muted">Sign in to your payroll workspace.</p><form method="post"><input type="hidden" name="csrf" value="{{ csrf }}"><label class="form-label">Username</label><input class="form-control form-control-lg mb-3" name="username" required><label class="form-label">Password</label><input class="form-control form-control-lg mb-4" type="password" name="password" required><button class="btn btn-primary btn-lg w-100">Sign in →</button></form><div class="demo mt-4"><b>Demo accounts</b><br>Admin: admin / admin123<br>HR: hr / hr123<br>Manager: manager / manager123<br>Employee: aarav / emp123<br><small>Change demo passwords after first login.</small></div></div></div>"""
ADMIN_DASH=r"""<div class="heading"><div><div class="eyebrow">WORKSPACE</div><h1>Good day, {{ session.username|title }}</h1><p>{% if session.role=='admin' %}System access, configuration and operational oversight.{% elif session.role=='hr' %}Employee records, compensation and payroll operations.{% else %}Team leave approvals and workforce overview.{% endif %}</p></div><span class="muted small">{{ today[:7] }}</span></div><div class="row g-3 mb-4">{% for label,val in [('Active employees',counts[0]),('Pending leave',counts[1]),('Payroll records',counts[2]),('Current month net payroll','₹%.2f'|format(counts[3]))] %}<div class="col-6 col-xl-3"><div class="stat"><small>{{ label }}</small><strong>{{ val }}</strong></div></div>{% endfor %}</div><div class="row g-3"><div class="col-xl-8"><div class="panel"><h2 class="title2 mb-3">Recent payroll</h2><div class="table-responsive"><table class="table"><tr><th>Employee</th><th>Month</th><th>Net salary</th></tr>{% for p in recent %}<tr><td>{{ p.first_name }} {{ p.last_name }}</td><td>{{ p.pay_month }}</td><td>₹{{ '%.2f'|format(p.net) }}</td></tr>{% else %}<tr><td colspan="3">No payroll records yet.</td></tr>{% endfor %}</table></div></div></div><div class="col-xl-4"><div class="panel quick"><h2 class="title2">Quick actions</h2>{% if session.role=='hr' %}<a href="/employees/add">＋ Add employee</a><a href="/attendance">＋ Record attendance</a><a href="/leaves">✓ HR final leave review</a><a href="/salary">₹ Manage compensation</a><a href="/payroll">▤ Generate payroll</a><a href="/reports">↗ HR reports</a>{% elif session.role=='admin' %}<a href="/admin/users">＋ Manage user accounts</a><a href="/admin/departments">⚙ System configuration</a><a href="/audit">▤ Audit log</a><a href="/backup">↓ Database backup</a><a href="/reports">↗ Operational reports</a>{% else %}<a href="/leaves">✓ Review team leave requests</a><a href="/employees">View employee directory</a>{% endif %}</div></div></div>"""
EMP_DASH=r"""<div class="heading"><div><div class="eyebrow">EMPLOYEE PORTAL</div><h1>Hello, {{ emp.first_name }}.</h1><p>Your personal payroll and leave information.</p></div></div><div class="row g-3"><div class="col-md-6"><div class="panel"><div class="eyebrow">EMPLOYEE DETAILS</div><h2 class="title2 mt-2">{{ emp.first_name }} {{ emp.last_name }}</h2><p class="muted">{{ emp.code }} · {{ emp.designation }} · {{ emp.department }}</p>{{ emp.email }}</div></div><div class="col-md-6"><div class="panel"><div class="eyebrow">LATEST NET SALARY</div>{% if p %}<h2 class="mt-2">₹{{ '%.2f'|format(p.net) }}</h2>{{ p.pay_month }} · <a href="/payslips/{{ p.id }}">View payslip</a>{% else %}<p class="muted">No payroll available.</p>{% endif %}</div></div></div><div class="panel mt-3"><h2 class="title2">Recent leave requests</h2><table class="table"><tr><th>Type</th><th>Dates</th><th>Days</th><th>Status</th></tr>{% for l in leaves %}<tr><td>{{ l.leave_type }}</td><td>{{ l.start_date }} – {{ l.end_date }}</td><td>{{ l.days }}</td><td>{{ l.status }}</td></tr>{% else %}<tr><td colspan="4">No leave requests.</td></tr>{% endfor %}</table><a href="/leaves/apply">Apply for leave →</a></div>"""
EMPLOYEES=r"""<div class="heading"><div><div class="eyebrow">PEOPLE</div><h1>Employee directory</h1><p>Maintain and monitor employee information.</p></div>{% if editable %}<a class="btn btn-primary" href="/employees/add">＋ Add employee</a>{% endif %}</div><div class="panel"><form class="d-flex gap-2 mb-3"><input class="form-control" name="q" value="{{ q }}" placeholder="Search employee"><button class="btn btn-outline-primary">Search</button></form><div class="table-responsive"><table class="table"><tr><th>Employee</th><th>Code</th><th>Department</th><th>Designation</th><th>Status</th>{% if editable %}<th></th>{% endif %}</tr>{% for e in rows %}<tr><td>{{ e.first_name }} {{ e.last_name }}<div class="small muted">{{ e.email }}</div></td><td>{{ e.code }}</td><td>{{ e.department }}</td><td>{{ e.designation }}</td><td>{{ e.status }}</td>{% if editable %}<td><a class="btn btn-sm btn-outline-primary" href="/employees/{{ e.id }}/edit">Edit</a>{% if e.status=='Active' %}<form method="post" class="d-inline" action="/employees/{{ e.id }}/deactivate"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn btn-sm btn-outline-danger">Deactivate</button></form>{% endif %}</td>{% endif %}</tr>{% else %}<tr><td colspan="6">No employees found.</td></tr>{% endfor %}</table></div></div>"""
EMP_FORM=r"""<div class="heading"><div><div class="eyebrow">EMPLOYEE RECORD</div><h1>{{ 'Edit' if emp else 'Add' }} employee</h1></div></div><form method="post" class="panel formbox"><input type="hidden" name="csrf" value="{{ csrf }}"><div class="row g-3">{% for field,label,typ in [('code','Employee code','text'),('first_name','First name','text'),('last_name','Last name','text'),('email','Email','email'),('phone','Phone','text'),('designation','Designation','text'),('joining_date','Joining date','date')] %}<div class="col-md-6"><label class="form-label">{{ label }}</label><input class="form-control" name="{{ field }}" type="{{ typ }}" value="{{ emp[field] if emp else '' }}" required></div>{% endfor %}<div class="col-md-6"><label class="form-label">Department</label><select class="form-select" name="department_id">{% for d in deps %}<option value="{{ d.id }}" {% if emp and emp.department_id==d.id %}selected{% endif %}>{{ d.name }}</option>{% endfor %}</select></div><div class="col-md-6"><label class="form-label">Status</label><select class="form-select" name="status"><option>Active</option><option {% if emp and emp.status=='Inactive' %}selected{% endif %}>Inactive</option></select></div></div><button class="btn btn-primary mt-4">Save employee</button> <a class="btn btn-light mt-4" href="/employees">Cancel</a>{% if not emp %}<p class="small muted mt-3">New employee login: email prefix / temporary password emp123.</p>{% endif %}</form>"""
ATTENDANCE=r"""<div class="heading"><div><div class="eyebrow">TIME & ATTENDANCE</div><h1>Attendance records</h1><p>Manual attendance; biometric devices are out of scope.</p></div></div>{% if editable %}<div class="panel mb-3"><h2 class="title2 mb-3">Record attendance</h2><form method="post" action="/attendance/save"><input type="hidden" name="csrf" value="{{ csrf }}"><div class="row g-2"><div class="col-md-3"><label class="form-label">Employee</label><select class="form-select" name="employee_id">{% for e in emps %}<option value="{{ e.id }}">{{ e.code }} · {{ e.first_name }} {{ e.last_name }}</option>{% endfor %}</select></div><div class="col-md-2"><label class="form-label">Date</label><input class="form-control" type="date" name="work_date" required></div><div class="col-md-2"><label class="form-label">Status</label><select class="form-select" name="status"><option>Present</option><option>Absent</option><option>Half-day</option><option>Weekly Off</option></select></div><div class="col-md-2"><label class="form-label">Check in</label><input class="form-control" type="time" name="check_in"></div><div class="col-md-2"><label class="form-label">Check out</label><input class="form-control" type="time" name="check_out"></div><div class="col-md-1 d-flex align-items-end"><button class="btn btn-primary">Save</button></div></div><input class="form-control mt-2" name="remarks" placeholder="Remarks"></form></div>{% endif %}<div class="panel"><form class="d-flex gap-2 mb-3"><input class="form-control" style="max-width:220px" type="month" name="month" value="{{ month }}"><button class="btn btn-outline-primary">Filter</button></form><table class="table"><tr><th>Date</th><th>Employee</th><th>Status</th><th>Check-in</th><th>Check-out</th></tr>{% for a in rows %}<tr><td>{{ a.work_date }}</td><td>{{ a.code }} {{ a.first_name }} {{ a.last_name }}</td><td>{{ a.status }}</td><td>{{ a.check_in }}</td><td>{{ a.check_out }}</td></tr>{% else %}<tr><td colspan="5">No records.</td></tr>{% endfor %}</table></div>"""
LEAVES=r"""<div class="heading"><div><div class="eyebrow">TIME OFF WORKFLOW</div><h1>Leave requests</h1><p>Manager first-stage review followed by HR final approval.</p></div>{% if session.role=='employee' %}<a class="btn btn-primary" href="/leaves/apply">＋ Apply for leave</a>{% endif %}</div><div class="panel"><div class="table-responsive"><table class="table align-middle"><tr><th>Employee</th><th>Type / Dates</th><th>Reason</th><th>Manager</th><th>HR</th><th>Final status</th>{% if review %}<th>Action</th>{% endif %}</tr>{% for l in rows %}<tr><td>{{ l.code }} {{ l.first_name }} {{ l.last_name }}</td><td>{{ l.leave_type }}<div class="small muted">{{ l.start_date }} – {{ l.end_date }} · {{ l.days }} day(s)</div></td><td>{{ l.reason }}</td><td><span class="badge text-bg-light">{{ l.manager_status }}</span>{% if l.manager_rejection_reason %}<div class="small text-danger mt-1">{{ l.manager_rejection_reason }}</div>{% endif %}</td><td><span class="badge text-bg-light">{{ l.hr_status }}</span>{% if l.hr_rejection_reason %}<div class="small text-danger mt-1">{{ l.hr_rejection_reason }}</div>{% endif %}</td><td><b>{{ l.status }}</b></td>{% if review %}<td style="min-width:210px">{% if stage=='manager' and l.status=='Pending Manager Approval' %}<form method="post" action="/leaves/{{ l.id }}/manager/approve" class="mb-2"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn btn-sm btn-success">Approve for HR</button></form><form method="post" action="/leaves/{{ l.id }}/manager/reject"><input type="hidden" name="csrf" value="{{ csrf }}"><input class="form-control form-control-sm mb-1" name="rejection_reason" minlength="5" placeholder="Reason required" required><button class="btn btn-sm btn-outline-danger">Reject</button></form>{% elif stage=='hr' and l.status=='Pending HR Approval' %}<form method="post" action="/leaves/{{ l.id }}/hr/approve" class="mb-2"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn btn-sm btn-success">Final approve</button></form><form method="post" action="/leaves/{{ l.id }}/hr/reject"><input type="hidden" name="csrf" value="{{ csrf }}"><input class="form-control form-control-sm mb-1" name="rejection_reason" minlength="5" placeholder="Reason required" required><button class="btn btn-sm btn-outline-danger">Final reject</button></form>{% else %}<span class="small muted">No action required</span>{% endif %}</td>{% endif %}</tr>{% else %}<tr><td colspan="7">No leave requests.</td></tr>{% endfor %}</table></div></div>"""
LEAVE_FORM=r"""<div class="heading"><div><div class="eyebrow">TIME OFF</div><h1>Apply for leave</h1></div></div><form method="post" class="panel formbox"><input type="hidden" name="csrf" value="{{ csrf }}"><div class="row g-3"><div class="col-md-6"><label class="form-label">Leave type</label><select class="form-select" name="leave_type"><option>Paid</option><option>Unpaid</option><option>Sick</option><option>Casual</option></select></div><div class="col-md-6"><label class="form-label">Reason</label><input class="form-control" name="reason" required></div><div class="col-md-6"><label class="form-label">Start date</label><input class="form-control" type="date" name="start_date" required></div><div class="col-md-6"><label class="form-label">End date</label><input class="form-control" type="date" name="end_date" required></div></div><button class="btn btn-primary mt-4">Submit request</button></form>"""
SALARY=r"""<div class="heading"><div><div class="eyebrow">COMPENSATION</div><h1>Salary structures</h1><p>Components used for monthly payroll.</p></div></div><div class="panel"><div class="table-responsive"><table class="table"><tr><th>Employee</th><th>Basic</th><th>HRA</th><th>Allowance</th><th>Bonus</th><th>PF</th><th>Other deduction</th>{% if editable %}<th></th>{% endif %}</tr>{% for s in rows %}<tr><td>{{ s.code }} {{ s.first_name }} {{ s.last_name }}</td>{% for f in ['basic','hra','allowance','bonus','pf','other_deduction'] %}<td>₹{{ '%.2f'|format(s[f]) }}</td>{% endfor %}{% if editable %}<td><a class="btn btn-sm btn-outline-primary" href="/salary/{{ s.employee_id }}">Edit</a></td>{% endif %}</tr>{% endfor %}</table></div><p class="small muted mt-3">Advanced income-tax management is excluded from this project scope. PF and other deductions are configurable.</p></div>"""
SALARY_FORM=r"""<div class="heading"><div><div class="eyebrow">COMPENSATION</div><h1>Salary structure · {{ emp.first_name }} {{ emp.last_name }}</h1></div></div><form method="post" class="panel formbox"><input type="hidden" name="csrf" value="{{ csrf }}"><div class="row g-3">{% for f,label in [('basic','Basic salary'),('hra','HRA'),('allowance','Allowance'),('bonus','Monthly bonus'),('pf','PF deduction'),('other_deduction','Other deduction')] %}<div class="col-md-6"><label class="form-label">{{ label }} (INR)</label><input class="form-control" type="number" min="0" step=".01" name="{{ f }}" value="{{ s[f] if s else 0 }}" required></div>{% endfor %}<div class="col-md-6"><label class="form-label">Effective from</label><input class="form-control" type="date" name="effective_from" value="{{ s.effective_from if s else emp.joining_date }}" required></div></div><button class="btn btn-primary mt-4">Save salary</button></form>"""
PAYROLL=r"""<div class="heading"><div><div class="eyebrow">PAYROLL PROCESSING</div><h1>Monthly payroll</h1><p>Attendance and unpaid-leave adjusted salary.</p></div></div>{% if editable %}<div class="panel mb-3"><form method="post" class="d-flex gap-2 align-items-end"><input type="hidden" name="csrf" value="{{ csrf }}"><div><label class="form-label">Payroll month</label><input class="form-control" type="month" name="month" value="{{ period }}" required></div><button class="btn btn-primary">Generate payroll</button></form><small class="muted">One payroll per employee per month. Existing records are not overwritten.</small></div>{% endif %}<div class="stat mb-3"><small>Net payroll total · {{ period }}</small><strong>₹{{ '%.2f'|format(total) }}</strong></div><div class="panel"><table class="table"><tr><th>Employee</th><th>Working days</th><th>Payable</th><th>Gross</th><th>Deductions</th><th>Net</th><th>Payslip</th></tr>{% for p in rows %}<tr><td>{{ p.code }} {{ p.first_name }} {{ p.last_name }}</td><td>{{ p.working_days }}</td><td>{{ p.payable_days }}</td><td>₹{{ '%.2f'|format(p.gross) }}</td><td>₹{{ '%.2f'|format(p.total_deduction+p.absence_deduction) }}</td><td><b>₹{{ '%.2f'|format(p.net) }}</b></td><td><a href="/payslips/{{ p.id }}">View</a></td></tr>{% else %}<tr><td colspan="7">No payroll for this month.</td></tr>{% endfor %}</table></div>"""
PAYSLIPS=r"""<div class="heading"><div><div class="eyebrow">PAY DOCUMENTS</div><h1>Payslips</h1></div></div><div class="panel"><table class="table"><tr><th>Period</th><th>Employee</th><th>Gross</th><th>Deductions</th><th>Net</th><th>Issued</th><th></th></tr>{% for p in rows %}<tr><td>{{ p.pay_month }}</td><td>{{ p.code }} {{ p.first_name }} {{ p.last_name }}</td><td>₹{{ p.gross }}</td><td>₹{{ p.total_deduction+p.absence_deduction }}</td><td>₹{{ p.net }}</td><td>{{ p.issue_date }}</td><td><a href="/payslips/{{ p.id }}">Open</a></td></tr>{% else %}<tr><td colspan="7">No payslips available.</td></tr>{% endfor %}</table></div>"""
SLIP=r"""<div class="d-flex justify-content-between mb-3"><div><div class="eyebrow">PAYSLIP · {{ p.pay_month }}</div><h1>Salary statement</h1></div><button class="btn btn-outline-primary" onclick="window.print()">Print / Save PDF</button></div><div class="panel" style="max-width:850px;margin:auto"><div class="d-flex justify-content-between"><h2 class="fw-bold">PAYROLL<span style="color:#315efb">.</span></h2><div class="text-end small">Payslip #{{ p.slip_id }}<br>Issued {{ p.issue_date }}</div></div><hr><div class="row"><div class="col-md-6"><small class="muted">EMPLOYEE</small><h5>{{ p.first_name }} {{ p.last_name }}</h5>{{ p.code }} · {{ p.email }}</div><div class="col-md-6"><small class="muted">ROLE / DEPARTMENT</small><h5>{{ p.designation }}</h5>{{ p.department }}<br>{{ p.pay_month }}</div></div><hr><h5>Earnings</h5><table class="table">{% for label,f in [('Basic','basic'),('HRA','hra'),('Allowance','allowance'),('Bonus','bonus'),('Gross salary','gross')] %}<tr><td>{{ label }}</td><td class="text-end">₹{{ '%.2f'|format(p[f]) }}</td></tr>{% endfor %}</table><h5 class="mt-3">Deductions</h5><table class="table">{% for label,f in [('PF','pf'),('Other deductions','other_deduction'),('Attendance / unpaid leave adjustment','absence_deduction')] %}<tr><td>{{ label }}</td><td class="text-end">₹{{ '%.2f'|format(p[f]) }}</td></tr>{% endfor %}</table><div class="net"><span>NET SALARY</span><b>₹{{ '%.2f'|format(p.net) }}</b></div></div>"""
REPORTS=r"""<div class="heading"><div><div class="eyebrow">INSIGHTS</div><h1>Reports</h1><p>Generate and export system reports.</p></div></div><div class="panel"><form method="get" class="d-flex gap-2 mb-3 flex-wrap"><select class="form-select" name="type" style="max-width:220px">{% for t,label in [('payroll','Payroll'),('employees','Employees'),('attendance','Attendance'),('leaves','Leave'),('salary','Salary')] %}<option value="{{ t }}" {% if typ==t %}selected{% endif %}>{{ label }} report</option>{% endfor %}</select><input class="form-control" style="max-width:200px" type="month" name="month" value="{{ period }}"><button class="btn btn-outline-primary">Generate</button><a class="btn btn-primary" href="/reports/export?type={{ typ }}&month={{ period }}">Export CSV</a></form><div class="table-responsive"><table class="table"><tr>{% if typ=='employees' %}<th>Code</th><th>Name</th><th>Email</th><th>Designation</th><th>Department</th><th>Status</th>{% elif typ=='attendance' %}<th>Date</th><th>Employee</th><th>Status</th><th>Check in</th><th>Check out</th>{% elif typ=='leaves' %}<th>Employee</th><th>Type</th><th>Dates</th><th>Days</th><th>Reason</th><th>Manager review</th><th>Manager rejection reason</th><th>HR review</th><th>HR rejection reason</th><th>Final status</th>{% elif typ=='salary' %}<th>Employee</th><th>Basic</th><th>HRA</th><th>Allowance</th><th>Bonus</th><th>PF</th><th>Other</th>{% else %}<th>Period</th><th>Employee</th><th>Gross</th><th>Deductions</th><th>Net</th>{% endif %}</tr>{% for r in rows %}<tr>{% if typ=='employees' %}<td>{{ r.code }}</td><td>{{ r.first_name }} {{ r.last_name }}</td><td>{{ r.email }}</td><td>{{ r.designation }}</td><td>{{ r.department }}</td><td>{{ r.status }}</td>{% elif typ=='attendance' %}<td>{{ r.work_date }}</td><td>{{ r.code }} {{ r.first_name }} {{ r.last_name }}</td><td>{{ r.status }}</td><td>{{ r.check_in }}</td><td>{{ r.check_out }}</td>{% elif typ=='leaves' %}<td>{{ r.code }} {{ r.first_name }} {{ r.last_name }}</td><td>{{ r.leave_type }}</td><td>{{ r.start_date }}–{{ r.end_date }}</td><td>{{ r.days }}</td><td>{{ r.reason }}</td><td>{{ r.manager_status }}</td><td>{{ r.manager_rejection_reason or '—' }}</td><td>{{ r.hr_status }}</td><td>{{ r.hr_rejection_reason or '—' }}</td><td>{{ r.status }}</td>{% elif typ=='salary' %}<td>{{ r.code }} {{ r.first_name }} {{ r.last_name }}</td><td>{{ r.basic }}</td><td>{{ r.hra }}</td><td>{{ r.allowance }}</td><td>{{ r.bonus }}</td><td>{{ r.pf }}</td><td>{{ r.other_deduction }}</td>{% else %}<td>{{ r.pay_month }}</td><td>{{ r.code }} {{ r.first_name }} {{ r.last_name }}</td><td>{{ r.gross }}</td><td>{{ r.total_deduction+r.absence_deduction }}</td><td>{{ r.net }}</td>{% endif %}</tr>{% else %}<tr><td colspan="7">No records found.</td></tr>{% endfor %}</table></div></div>"""
PROFILE=r"""<div class="heading"><div><div class="eyebrow">ACCOUNT</div><h1>My account</h1></div></div><div class="row g-3"><div class="col-md-6"><form method="post" class="panel"><input type="hidden" name="csrf" value="{{ csrf }}"><label class="form-label">Username</label><input class="form-control mb-3" name="username" value="{{ u.username }}" minlength="3" required><label class="form-label">Role</label><input class="form-control mb-3" value="{{ 'Manager' if u.role=='management' else u.role|title }}" disabled><button class="btn btn-primary">Update profile</button></form></div><div class="col-md-6"><div class="panel"><h2 class="title2">Security</h2><p class="muted">Change your password regularly.</p><a class="btn btn-outline-primary" href="/password">Change password</a></div></div></div>"""
PASSWORD=r"""<div class="heading"><div><div class="eyebrow">SECURITY</div><h1>Change password</h1></div></div><form method="post" class="panel formbox"><input type="hidden" name="csrf" value="{{ csrf }}">{% for f,l in [('current','Current password'),('new','New password'),('confirm','Confirm new password')] %}<label class="form-label">{{ l }}</label><input class="form-control mb-3" type="password" name="{{ f }}" {% if f!='current' %}minlength="8"{% endif %} required>{% endfor %}<button class="btn btn-primary">Save password</button></form>"""
NOTICES=r"""<div class="heading"><div><div class="eyebrow">UPDATES</div><h1>Notifications</h1></div></div><div class="panel">{% for n in rows %}<div class="py-3 border-bottom">{{ n.message }}<div class="small muted">{{ n.created_at }}</div></div>{% else %}<p class="muted">No notifications.</p>{% endfor %}</div>"""
AUDIT=r"""<div class="heading"><div><div class="eyebrow">SECURITY & TRACEABILITY</div><h1>Audit log</h1></div></div><div class="panel"><table class="table"><tr><th>Date</th><th>User</th><th>Action</th><th>Details</th></tr>{% for a in rows %}<tr><td>{{ a.created_at }}</td><td>{{ a.username or 'System' }}</td><td>{{ a.action }}</td><td>{{ a.details }}</td></tr>{% endfor %}</table></div>"""
ADMIN_USERS=r"""<div class="heading"><div><div class="eyebrow">ADMINISTRATION</div><h1>User access management</h1><p>Create accounts, assign roles and activate or deactivate access.</p></div></div><div class="panel mb-3"><h2 class="title2">Create user account</h2><form method="post" class="row g-2"><input type="hidden" name="csrf" value="{{ csrf }}"><div class="col-md-3"><input class="form-control" name="username" placeholder="Username" minlength="3" required></div><div class="col-md-3"><input class="form-control" type="password" name="password" placeholder="Temporary password (8+ chars)" minlength="8" required></div><div class="col-md-3"><select class="form-select" name="role"><option value="admin">Admin</option><option value="hr">HR</option><option value="management">Manager</option><option value="employee">Employee</option></select></div><div class="col-md-2"><select class="form-select" name="employee_id"><option value="">No employee link</option>{% for e in employees %}<option value="{{ e.id }}">{{ e.code }} · {{ e.first_name }} {{ e.last_name }}</option>{% endfor %}</select></div><div class="col-md-1"><button class="btn btn-primary w-100">Add</button></div></form><p class="small muted mt-2 mb-0">Employee accounts should be linked to an employee record. Only one login can be linked to each employee.</p></div><div class="panel"><div class="table-responsive"><table class="table"><tr><th>Username</th><th>Role</th><th>Linked employee</th><th>Status</th><th>Access</th></tr>{% for u in users %}<tr><td>{{ u.username }}</td><td>{{ {'management':'Manager','hr':'HR','admin':'Admin','employee':'Employee'}[u.role] }}</td><td>{{ (u.code ~ ' · ' ~ u.first_name ~ ' ' ~ u.last_name) if u.employee_id else '—' }}</td><td>{{ u.status }}</td><td>{% if u.id != session.uid %}<form method="post" action="/admin/users/{{ u.id }}/status"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn btn-sm btn-outline-{{ 'danger' if u.status=='Active' else 'success' }}">{{ 'Deactivate' if u.status=='Active' else 'Activate' }}</button></form>{% else %}<span class="small muted">Current account</span>{% endif %}</td></tr>{% endfor %}</table></div></div>"""
ADMIN_DEPARTMENTS=r"""<div class="heading"><div><div class="eyebrow">SYSTEM CONFIGURATION</div><h1>Departments</h1><p>Admin controls system-level organization settings.</p></div></div><div class="panel mb-3"><form method="post" class="d-flex gap-2"><input type="hidden" name="csrf" value="{{ csrf }}"><input class="form-control" name="name" placeholder="New department name" required><button class="btn btn-primary">Add department</button></form></div><div class="panel"><table class="table"><tr><th>Department</th><th>Employees assigned</th></tr>{% for d in rows %}<tr><td>{{ d.name }}</td><td>{{ d.employee_count }}</td></tr>{% endfor %}</table></div>"""
ERROR=r"""<div class="text-center py-5"><h1 class="display-2">{{ code }}</h1><h2>{{ msg }}</h2><a class="btn btn-primary mt-3" href="/dashboard">Return</a></div>"""

init_db()

if __name__ == "__main__":
    app.run(debug=True)
