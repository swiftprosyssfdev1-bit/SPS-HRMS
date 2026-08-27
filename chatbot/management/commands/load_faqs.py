"""
Bulk-loads a starter knowledge base of FAQs for the SPS Assist chatbot.

Usage:
    python manage.py load_faqs

Safe to re-run: matches on `question` and updates keywords/answer/target_roles/
priority in place rather than creating duplicates, so you can edit this file
and re-run it any time you want to add more or tweak existing answers.

NOTE: This file only covers static/policy/how-it-works knowledge (FAQ table).
Anything that depends on a specific employee's live data (leave balance,
"did I punch in today", "what's my salary", "is my leave approved", etc.)
is already handled separately by the dynamic intent functions in
chatbot/matcher.py and deliberately NOT duplicated here.
"""
from django.core.management.base import BaseCommand

from chatbot.models import FAQ


# Each entry: (question, keywords, answer, target_roles, priority)
# target_roles: 'ALL' or comma-separated role codes from core.models.User:
#   EMPLOYEE, MANAGER, PROJECT_MANAGER, GENERAL_MANAGER, HR, SENIOR_HR,
#   ADMIN, COO, CTO
FAQS = [

    # ------------------------------------------------------------------
    # 1. General / What is SPS HRMS
    # ------------------------------------------------------------------
    (
        "What is SPS HRMS?",
        "what is this, what is prostaff, about sps hrms, what is this system, what is this app",
        "SPS HRMS is SwiftProSys's internal HR management system. You can use it to mark "
        "attendance, apply for leave/permission/WFH, view payslips, track daily production "
        "(if applicable to your role), raise queries to HR/Manager, and more — all from the "
        "sidebar menu.",
        "ALL", 5,
    ),
    (
        "How do I update my profile?",
        "edit profile, change my details, update my details, update personal details, "
        "change address, update phone number, update bank details",
        "Go to My Profile from the sidebar, then use Edit Profile to update your personal "
        "details. Some identity fields (like bank details or Aadhar) may require HR or your "
        "Manager to make the change — if a field looks locked, ask HR.",
        "ALL", 0,
    ),
    (
        "How do I raise a query to HR or my Manager?",
        "how to contact hr, how to ask hr, how to message manager, raise a complaint, "
        "raise an issue, contact support",
        "Go to Ask a Query, choose whether it's for your Manager or HR, type your message, "
        "and submit. You can track replies under the same page.",
        "ALL", 0,
    ),

    # ------------------------------------------------------------------
    # 2. Leave Management (policy/process — not personal balances)
    # ------------------------------------------------------------------
    (
        "What types of leave are available?",
        "leave types, kinds of leave, casual leave, earned leave, sick leave, cl el sl",
        "SPS HRMS tracks three leave types: Casual Leave (CL), Earned Leave (EL), and Sick "
        "Leave. Your individual balance for each is shown under My Leaves.",
        "ALL", 0,
    ),
    (
        "How does leave approval work?",
        "leave approval process, how is leave approved, leave approval flow, "
        "how does leave get approved",
        "When you apply for leave/permission/WFH it goes to your reporting Manager first "
        "(or the CTO, if you're in the Software department). Once approved by your manager, "
        "it may also require HR approval depending on the leave type and your branch's "
        "workflow. You can track the current stage under My Leaves.",
        "ALL", 0,
    ),
    (
        "How can I cancel a leave request?",
        "cancel leave, withdraw leave, delete leave request, cancel my leave",
        "Open My Leaves and look for a Cancel/Withdraw option next to a pending request. "
        "Once a leave is already approved, cancelling it may need HR's help — raise a query "
        "if you don't see a cancel option.",
        "ALL", 0,
    ),
    (
        "What happens if my leave is rejected?",
        "leave rejected, why was my leave rejected, leave denied",
        "If your leave/permission/WFH request is rejected, it will show as Rejected under My "
        "Leaves. The approver may leave a reason. You can raise a query to your Manager or HR "
        "if you'd like more details or want to reapply.",
        "ALL", 0,
    ),
    (
        "Where can I see my approved leaves?",
        "see approved leaves, view approved leaves, leave history",
        "All your leave/permission/WFH requests — approved, pending, or rejected — are listed "
        "under My Leaves, most recent first.",
        "ALL", 0,
    ),

    # ------------------------------------------------------------------
    # 3. Attendance (policy — not "did I punch in today")
    # ------------------------------------------------------------------
    (
        "What time should I punch in?",
        "punch in time, office timing, shift timing, work hours, reporting time",
        "Punch-in timing follows your branch/department's shift schedule. Check with your "
        "Manager or HR for your specific shift timing, since this can vary by branch.",
        "ALL", 0,
    ),
    (
        "What happens if I forget to punch in?",
        "forgot to punch in, missed punch, missed attendance, forgot to punch",
        "If you forget to punch in or out, raise a query to HR with the date and correct "
        "time — attendance corrections are handled by HR, not automatically by the system.",
        "ALL", 0,
    ),
    (
        "How is attendance calculated?",
        "how is attendance calculated, attendance calculation, how does attendance work",
        "Attendance is based on your punch-in and punch-out times each day. Total hours "
        "worked, late arrivals, half-days, and Sundays/holidays are all tracked automatically "
        "from your punch records and shown on your Monthly Attendance page.",
        "ALL", 0,
    ),
    (
        "What is late attendance?",
        "late attendance, what counts as late, late marking",
        "Late attendance is when your punch-in time is after your shift's scheduled start "
        "time. Repeated late marks may be reviewed by your Manager or HR as per company "
        "policy.",
        "ALL", 0,
    ),
    (
        "What is half-day attendance?",
        "half day, what is half day, half day attendance",
        "A half-day is recorded when your total working hours for the day fall below the "
        "full-day threshold set for your role/department. Contact HR if you think a half-day "
        "was marked incorrectly.",
        "ALL", 0,
    ),
    (
        "How is overtime calculated?",
        "overtime calculation, how is overtime counted, ot calculation",
        "Overtime/Sunday work needs prior permission (see Overtime/Sunday Permission). Once "
        "authorized and your attendance is punched, the extra hours are calculated from your "
        "punch records for that day.",
        "ALL", 0,
    ),
    (
        "How can I correct an attendance mistake?",
        "correct attendance, fix attendance, wrong attendance, attendance mistake, "
        "wrong punch time",
        "Attendance records can't be edited by employees directly — raise a query to HR with "
        "the date and what needs to be corrected, and they'll update it.",
        "ALL", 0,
    ),

    # ------------------------------------------------------------------
    # 4. Hourly Production Tracker
    # ------------------------------------------------------------------
    (
        "What is the hourly tracker?",
        "what is hourly tracker, what is the tracker, production tracker meaning",
        "The Hourly/Daily Tracker is where production-department employees log their daily "
        "output against a project's target. It's used to calculate daily efficiency and "
        "feeds into appraisals.",
        "ALL", 0,
    ),
    (
        "Who needs to fill the hourly tracker?",
        "who fills hourly tracker, who uses the tracker, is tracker mandatory for me",
        "The daily tracker is for employees on production/software projects with a daily "
        "target set. If your project doesn't have a daily target configured, you likely don't "
        "need to fill it — check with your Team Leader or PM.",
        "ALL", 0,
    ),
    (
        "How do I enter my hourly production count?",
        "enter production, submit production, daily entry, log production, "
        "fill hourly tracker",
        "Go to My Hourly Tracker (Daily Entry) and enter your achieved count for the day "
        "against your assigned project. Submit before the cutoff your team follows.",
        "ALL", 0,
    ),
    (
        "What is the daily target?",
        "what is daily target, daily target meaning",
        "The Daily Target is the expected production count set per project — every team "
        "member on that project shares the same target unless your lead/PM sets a different "
        "one for you specifically.",
        "ALL", 0,
    ),
    (
        "How is efficiency calculated?",
        "efficiency calculation, how is efficiency calculated, what is efficiency percentage",
        "Efficiency % is calculated from your achieved production count against the day's "
        "target for that project (achieved ÷ target × 100). It's shown on your tracker "
        "history and used in appraisals.",
        "ALL", 0,
    ),
    (
        "Can I edit my previous tracker entry?",
        "edit tracker entry, edit previous production, change yesterday's entry",
        "This depends on your team's settings — some entries lock after submission. If you "
        "need a correction, ask your Team Leader/PM or raise a query to HR.",
        "ALL", 0,
    ),
    (
        "What happens if I worked on multiple projects in a day?",
        "multiple projects same day, worked on two projects, split day between projects",
        "If you're assigned to more than one current project, you can log entries for each "
        "project you worked on that day under the Hourly Tracker.",
        "ALL", 0,
    ),
    (
        "What should QA employees enter in the tracker?",
        "qa tracker, qa entry, what does qa fill",
        "QA employees log their daily checked/reviewed count the same way as production "
        "entries, against the project they're assigned to review.",
        "ALL", 0,
    ),
    (
        "Who can modify an employee's tracker entry?",
        "who can edit tracker, who can change production entry, modify tracker",
        "Team Leaders, Project Managers, and HR/Admin generally have permission to review or "
        "correct tracker entries for their team. Regular employees can only edit their own, "
        "and only within the allowed window.",
        "ALL", 0,
    ),

    # ------------------------------------------------------------------
    # 5. Appraisal & Increment
    # ------------------------------------------------------------------
    (
        "What is an appraisal?",
        "what is appraisal, appraisal meaning",
        "An appraisal is a periodic (typically 12-month) performance review that combines "
        "your production/QA targets vs. achievement, efficiency, attendance, and manual "
        "scores (Quality, Attrition, Leadership, Process) into one report.",
        "ALL", 0,
    ),
    (
        "What is the appraisal period?",
        "appraisal period, appraisal cycle length, how long is appraisal",
        "An appraisal cycle covers 12 months starting from a chosen start month — the system "
        "auto-fills the remaining 11 months and the end date once the start month is picked.",
        "ALL", 0,
    ),
    (
        "How is the monthly target considered in appraisal?",
        "target in appraisal, how is target calculated for appraisal",
        "The appraisal pulls each month's production/QA target vs. what was actually "
        "achieved, straight from your monthly tracker records — you don't need to enter this "
        "manually.",
        "ALL", 0,
    ),
    (
        "How is attendance considered in appraisal?",
        "attendance in appraisal, working days in appraisal",
        "Your attendance and working days for each month of the appraisal cycle are factored "
        "in automatically from your attendance records.",
        "ALL", 0,
    ),
    (
        "What is the difference between target and achievement in appraisal?",
        "target vs achievement, difference between target and achieved",
        "Target is the expected daily/monthly production count for your project; Achievement "
        "is what you actually produced. The appraisal compares the two across the whole "
        "cycle to gauge performance.",
        "ALL", 0,
    ),
    (
        "What is the role of HR in appraisal?",
        "hr role in appraisal, what does hr do in appraisal",
        "HR runs the Appraisal Calculator to generate the 12-month draft appraisal, fills in "
        "manual scores where needed, and exports the final report.",
        "ALL", 0,
    ),
    (
        "What is the role of PM in appraisal?",
        "pm role in appraisal, project manager appraisal role",
        "A Project Manager reviews their team's production/QA performance and provides input "
        "used for manual scoring (Quality, Leadership, etc.) during appraisal and increment "
        "review.",
        "ALL", 0,
    ),
    (
        "What is the role of GM in appraisal and increment?",
        "gm role in appraisal, gm role in increment, general manager appraisal",
        "A General Manager reviews the performance of the Project Managers (and other staff) "
        "reporting to them, and provides feedback used by HR when preparing an increment "
        "proposal for that person.",
        "ALL", 0,
    ),
    (
        "What is the role of COO in appraisal and increment?",
        "coo role in appraisal, coo role in increment",
        "The COO gives final approval on increment proposals and appraisal-linked salary "
        "changes after HR has reviewed the manager/GM feedback and documentation.",
        "ALL", 0,
    ),
    (
        "How does the increment approval process work?",
        "increment approval process, how does increment work, increment process, "
        "how is increment approved",
        "HR initiates an increment request and routes it to the employee's reporting "
        "manager/GM for feedback. The manager/GM reviews performance and submits their "
        "feedback and any supporting records back to HR. HR then reviews everything and "
        "forwards the proposal to the COO for final approval before it's applied to salary.",
        "ALL", 0,
    ),
    (
        "How does the PM increment process work?",
        "pm increment process, project manager increment, how is pm increment decided",
        "HR initiates the increment request for the PM and sends it to the GM the PM reports "
        "to. The GM reviews the PM's performance and submits feedback and relevant work "
        "records to HR. HR reviews this and forwards the increment proposal to the COO for "
        "final approval.",
        "ALL", 0,
    ),

    # ------------------------------------------------------------------
    # 6. Payroll
    # ------------------------------------------------------------------
    (
        "What is the payroll cycle?",
        "payroll cycle, when is salary processed, salary cycle, salary dates",
        "Payroll runs on a monthly cycle. HR processes each month's run after attendance and "
        "any pending corrections are finalized. Contact HR for your branch's exact payout "
        "date.",
        "ALL", 0,
    ),
    (
        "What is gross salary?",
        "gross salary meaning, what is gross",
        "Gross salary is your total pay before deductions — Basic + HRA + Conveyance + Other "
        "Allowances + Bonus, as set in your Salary Structure.",
        "ALL", 0,
    ),
    (
        "What are allowances?",
        "what are allowances, types of allowances, hra conveyance meaning",
        "Allowances are additional pay components on top of Basic salary — common ones in "
        "SPS HRMS are HRA, Conveyance, Other Allowances, and Bonus. You can view yours under "
        "your Salary Structure, and request a change via Allowance Requests.",
        "ALL", 0,
    ),
    (
        "What are deductions?",
        "what are deductions, salary deductions, what is deducted from salary",
        "Deductions reduce your gross pay to arrive at net/take-home salary — for example "
        "Loss of Pay (LOP) for unapproved absence. Check your payslip for the exact "
        "breakdown for a given month.",
        "ALL", 0,
    ),
    (
        "How are attendance hours considered in payroll?",
        "attendance in payroll, how does attendance affect salary, lop meaning",
        "Days without approved attendance (no punch, no approved leave) are marked LOP (Loss "
        "of Pay) and reduce that month's salary accordingly. Approved leave/holidays don't "
        "count against you.",
        "ALL", 0,
    ),
    (
        "Can employees download their salary information?",
        "download salary slip, download payslip, salary information download",
        "Yes — go to Payslips to view and download your payslip for any processed month.",
        "ALL", 0,
    ),

    # ------------------------------------------------------------------
    # 7. Roles & Responsibilities — Employee
    # ------------------------------------------------------------------
    (
        "What can an employee access in SPS HRMS?",
        "employee access, what can i access, employee permissions",
        "As an Employee you can view your own profile, attendance, leave, payslips, "
        "projects, and hourly tracker (if applicable), apply for leave/permission/WFH, and "
        "raise queries to your Manager or HR. You don't have access to other employees' "
        "personal data or approval screens.",
        "EMPLOYEE", 0,
    ),
    (
        "Can an employee approve leave?",
        "employee approve leave, can employee approve leave requests",
        "No. Leave approval is done by your reporting Manager, and in some cases also HR/COO "
        "depending on the leave type. Employees can only apply for and track their own "
        "requests.",
        "EMPLOYEE", 0,
    ),
    (
        "Can an employee edit attendance records?",
        "employee edit attendance, can employee change attendance",
        "No, employees can't edit attendance directly. If there's a mistake, raise a query "
        "to HR with the correction needed.",
        "EMPLOYEE", 0,
    ),

    # ------------------------------------------------------------------
    # 7b. Roles & Responsibilities — PM
    # ------------------------------------------------------------------
    (
        "What can a Project Manager manage?",
        "pm permissions, what can pm manage, project manager access",
        "A Project Manager manages their assigned project(s) and team: reviewing/approving "
        "their team's leave and overtime requests, tracking hourly production, giving "
        "onboarding training feedback for new joiners, and providing increment/appraisal "
        "feedback for their team.",
        "PROJECT_MANAGER", 0,
    ),
    (
        "How does a PM handle production employees?",
        "pm production management, how does pm manage team production",
        "A PM can view their team's daily tracker entries and production/QA targets under "
        "the Tracker Dashboard, and set or adjust the project's daily target.",
        "PROJECT_MANAGER", 0,
    ),
    (
        "How does a PM provide appraisal or increment feedback?",
        "pm increment feedback, pm appraisal feedback, how pm gives feedback",
        "When HR or a GM requests feedback for a team member's increment/appraisal, it "
        "appears under your Report Inbox or Feedback Requests — fill in the requested "
        "details there.",
        "PROJECT_MANAGER", 0,
    ),

    # ------------------------------------------------------------------
    # 7c. Roles & Responsibilities — GM
    # ------------------------------------------------------------------
    (
        "What employees can a GM see?",
        "gm visibility, what employees does gm see, gm scope",
        "A General Manager sees only their own reporting hierarchy: their direct reports "
        "(including Project Managers and any employees/assistants placed directly under "
        "them), plus each of those PMs' own project/department teams. A GM never sees "
        "employees under a different GM, and never the whole branch.",
        "GENERAL_MANAGER", 0,
    ),
    (
        "How are employees assigned under a GM?",
        "assign employee to gm, how gm gets employees, gm team assignment",
        "HR assigns an employee's reporting manager to a GM directly (via the employee's "
        "Reporting Manager field), or the employee ends up under a GM indirectly by "
        "reporting to a PM who reports to that GM.",
        "GENERAL_MANAGER,HR,SENIOR_HR,ADMIN", 0,
    ),
    (
        "Can a GM see employees without projects?",
        "gm employees without projects, gm see unassigned employees",
        "Yes. Project assignment is optional and separate from reporting structure — if HR "
        "has set the employee's reporting manager to this GM (directly, or via a PM under "
        "this GM), the GM will see them in their Directory even without a project assigned.",
        "GENERAL_MANAGER,HR,SENIOR_HR,ADMIN", 0,
    ),
    (
        "Can a GM approve PM leave?",
        "gm approve pm leave, does gm approve manager leave",
        "Yes — a GM can approve leave/permission/WFH requests for the PMs and other staff "
        "who report directly to them, the same way any manager approves their team's leave.",
        "GENERAL_MANAGER", 0,
    ),
    (
        "What is the GM's role in PM increment?",
        "gm role pm increment, gm increment feedback for pm",
        "When HR initiates an increment for a PM, it's routed to that PM's GM for "
        "performance feedback and supporting records before HR forwards it to the COO.",
        "GENERAL_MANAGER", 0,
    ),
    (
        "Can a GM have an Assistant or Team Member directly under them?",
        "gm assistant, gm team member, employee directly under gm, gm direct report",
        "Yes. A GM can have Project Managers, Assistants, Team Members, or other employees "
        "placed directly under them — these don't need to belong to any project to be part "
        "of the GM's team.",
        "GENERAL_MANAGER,HR,SENIOR_HR,ADMIN", 0,
    ),

    # ------------------------------------------------------------------
    # 7d. Roles & Responsibilities — HR
    # ------------------------------------------------------------------
    (
        "What can HR manage in SPS HRMS?",
        "hr permissions, what can hr do, hr access",
        "HR (and Senior HR) can onboard employees, manage leave/attendance/payroll records "
        "for their branch, review resignation requests, manage salary structures and "
        "allowances, run appraisals, and initiate increment requests.",
        "HR,SENIOR_HR", 0,
    ),
    (
        "How does HR initiate an increment?",
        "hr initiate increment, how hr starts increment, hr create increment request",
        "Go to Increments → New Increment Request, select the employee, and submit — this "
        "routes it to their reporting manager/GM for feedback before it comes back to HR and "
        "then the COO.",
        "HR,SENIOR_HR", 0,
    ),
    (
        "How can HR assign an employee to a GM?",
        "hr assign employee to gm, set reporting manager, change reporting manager",
        "When onboarding or editing an employee, set their Reporting Manager field to the GM "
        "(or to a PM who reports to that GM). This determines both approval routing and what "
        "the GM sees in their Directory.",
        "HR,SENIOR_HR,ADMIN", 0,
    ),
    (
        "How does HR manage employee records?",
        "hr manage records, hr edit employee, hr employee management",
        "Use Employee Directory to search/filter employees, and open an employee's profile "
        "to edit their details, department, branch, reporting manager, and status.",
        "HR,SENIOR_HR,ADMIN", 0,
    ),

    # ------------------------------------------------------------------
    # 7e. Roles & Responsibilities — COO
    # ------------------------------------------------------------------
    (
        "What requests require COO approval?",
        "coo approval, what does coo approve, coo required for",
        "The COO gives final approval on increment/salary proposals after HR review, and may "
        "also approve certain leave requests depending on the workflow for that branch/role.",
        "COO", 0,
    ),
    (
        "How does the COO handle PM leave?",
        "coo pm leave, coo approve pm leave",
        "PM leave typically routes through their GM first; some leave types then also need "
        "COO sign-off, visible under the COO Dashboard's Leave Approval tab.",
        "COO", 0,
    ),
    (
        "How does the COO handle increment proposals?",
        "coo increment proposal, coo review increment",
        "Increment proposals reach the COO after HR has reviewed the manager/GM's feedback. "
        "The COO gives final approval under the COO Dashboard's Increment Approval tab before "
        "the change is applied to salary.",
        "COO", 0,
    ),

    # ------------------------------------------------------------------
    # 8. Projects
    # ------------------------------------------------------------------
    (
        "What is a project in SPS HRMS?",
        "what is a project, project meaning",
        "A Project represents a piece of work with its own Manager, Lead, and daily "
        "production target. Employees are linked to a project through a Project Assignment, "
        "which is how their daily tracker entries and targets get tied to that project.",
        "ALL", 0,
    ),
    (
        "How are employees assigned to projects?",
        "assign employee to project, project assignment, add employee to project",
        "A Project Manager, Lead, or HR/Admin assigns an employee to a project — this creates "
        "a Project Assignment linking the employee to that project, with a role on the "
        "project and a start date. An assignment can later be marked as no longer current if "
        "the employee moves off it.",
        "ALL", 0,
    ),
    (
        "Can an employee work on multiple projects?",
        "multiple projects, more than one project, work on two projects",
        "Yes. An employee can have more than one current Project Assignment at the same "
        "time, and can log hourly tracker entries against each project they're actively on.",
        "ALL", 0,
    ),
    (
        "Can an employee be under a GM without being on a project?",
        "employee no project under gm, gm without project, no project assigned to gm, "
        "no project will i appear under gm, not assigned to project gm, "
        "will i show up under my gm without project, i don't have a project, "
        "what if an employee has no project, can my gm manage me if i'm not assigned to "
        "a project, will i still appear under my gm, am i still under my gm without a project",
        "Yes. Reporting to a GM (directly, or through a PM who reports to that GM) is "
        "separate from project assignment. An employee with no current project still "
        "belongs to their GM's team and will still appear in that GM's Directory.",
        "ALL", 0,
    ),

    # ------------------------------------------------------------------
    # 9. GM Hierarchy (expanded)
    # ------------------------------------------------------------------
    (
        "Can a GM manage Project Managers?",
        "gm manage pm, can gm manage project managers, gm supervise pm",
        "Yes. Project Managers who report to a GM are part of that GM's team — the GM can "
        "approve their leave, see their tracker/production data, and provides feedback when "
        "HR initiates an increment for that PM.",
        "GENERAL_MANAGER", 0,
    ),
    (
        "Can a GM have direct employees, not just PMs?",
        "gm direct employees, employees directly under gm, gm non-pm reports",
        "Yes. A GM's team isn't limited to Project Managers — employees, assistants, or team "
        "members can be set to report directly to a GM as well.",
        "GENERAL_MANAGER,HR,SENIOR_HR,ADMIN", 0,
    ),
    (
        "Can a GM see another GM's employees?",
        "gm see other gm employees, can gm see different gm team, cross gm visibility",
        "No. A GM's visibility is strictly bounded to their own reporting hierarchy — they "
        "never see employees who report to a different GM, and never the whole branch.",
        "GENERAL_MANAGER", 0,
    ),

    # ------------------------------------------------------------------
    # 10. PM Increment Process (expanded)
    # ------------------------------------------------------------------
    (
        "Who initiates a PM's increment request?",
        "who starts pm increment, who initiates pm increment",
        "HR initiates the increment request for a PM — a PM does not raise their own "
        "increment request.",
        "ALL", 0,
    ),
    (
        "Does a PM submit their own increment request?",
        "pm submit own increment, can pm raise own increment, pm request own raise",
        "No. Increment requests for a PM are initiated by HR, not by the PM themselves. The "
        "PM's own GM is the one who provides performance feedback for it.",
        "PROJECT_MANAGER", 0,
    ),
    (
        "What does the GM provide to HR during a PM's increment review?",
        "gm provides hr increment, what gm submits for increment, gm feedback increment",
        "The GM reviews the PM's performance and submits feedback along with relevant work "
        "records/files to HR to support the increment decision.",
        "GENERAL_MANAGER,HR,SENIOR_HR", 0,
    ),
    (
        "What records should the GM submit for a PM's increment?",
        "what records gm submits, gm supporting documents increment, gm files for increment",
        "Any performance-related documentation that supports the feedback — e.g. production/"
        "efficiency records, project outcomes, or notes relevant to the PM's review period. "
        "Exact expectations may vary by branch/HR policy.",
        "GENERAL_MANAGER", 0,
    ),
    (
        "What does HR do after receiving the GM's increment feedback?",
        "hr after gm feedback, hr review gm feedback, hr next step increment",
        "HR reviews the GM's feedback and records, then forwards the increment proposal to "
        "the COO for final approval.",
        "HR,SENIOR_HR", 0,
    ),
    (
        "Who receives the final increment proposal for approval?",
        "who approves final increment, who gives final increment approval",
        "The COO gives the final approval on increment proposals, after HR has reviewed the "
        "manager/GM's feedback.",
        "ALL", 0,
    ),

    # ------------------------------------------------------------------
    # 11. PM Leave (expanded)
    # ------------------------------------------------------------------
    (
        "Who approves a PM's leave?",
        "who approves pm leave, pm leave approver",
        "A PM's leave/permission/WFH request goes to their reporting GM for approval (unless "
        "the PM is in the Software department, in which case it routes to the CTO).",
        "ALL", 0,
    ),
    (
        "Can the GM see a PM's leave status?",
        "gm see pm leave status, gm view pm leave",
        "Yes — a GM can see the leave status of PMs and other staff who report to them under "
        "Team Leave Status.",
        "GENERAL_MANAGER", 0,
    ),
    (
        "What happens when a PM's leave is approved or rejected?",
        "pm leave approved rejected, pm leave outcome",
        "The PM's leave request status updates to Approved or Rejected under their own My "
        "Leaves page, the same as any employee's request — the GM's decision is final at "
        "that stage unless the branch's workflow also requires HR/COO sign-off.",
        "ALL", 0,
    ),

    # ------------------------------------------------------------------
    # 12. Allowances (expanded)
    # ------------------------------------------------------------------
    (
        "Who receives allowance requests?",
        "who approves allowance, who reviews allowance request, allowance approver",
        "Employees in a Production department have their allowance request go to their "
        "Project Manager first, then HR for final approval. Everyone else's request goes "
        "straight to HR.",
        "ALL", 0,
    ),
    (
        "Can I cancel or delete an allowance request after submitting it?",
        "delete allowance request, cancel allowance request, withdraw allowance request",
        "There's no self-service delete/cancel option once an allowance request is "
        "submitted. If you need to withdraw or correct one, raise a query to HR (or your PM, "
        "if it's still pending PM approval) and ask them to reject it.",
        "ALL", 0,
    ),
    (
        "Can a GM manage allowance requests?",
        "gm allowance requests, gm approve allowance, gm review allowance",
        "No. Allowance request approval belongs to HR (and, for Production employees, their "
        "Project Manager) — a GM does not review or manage allowance requests for staff "
        "under them.",
        "GENERAL_MANAGER", 0,
    ),

    # ------------------------------------------------------------------
    # 13. HR Operations (expanded)
    # ------------------------------------------------------------------
    (
        "Can HR assign an employee without a project?",
        "hr assign employee no project, onboard employee without project",
        "Yes. Project assignment is optional when onboarding or editing an employee — HR can "
        "set the employee's reporting manager/branch/department without linking them to any "
        "project.",
        "HR,SENIOR_HR,ADMIN", 0,
    ),
    (
        "How does HR initiate an increment for a regular employee?",
        "hr initiate employee increment, employee increment request, non-pm increment",
        "The same way as for any role: HR goes to Increments → New Increment Request, "
        "selects the employee, and submits — it routes to their reporting manager for "
        "feedback before returning to HR and then the COO.",
        "HR,SENIOR_HR", 0,
    ),

    # ------------------------------------------------------------------
    # 14. Employee Lifecycle
    # ------------------------------------------------------------------
    (
        "How does employee onboarding work?",
        "employee onboarding, onboard new employee, new joiner process",
        "HR onboards a new employee via Onboard Employee — entering their basic details, "
        "branch, department, and reporting manager. The employee's status starts as "
        "Onboarding and moves to Active once onboarding is complete. Managers also submit "
        "onboarding training feedback for new joiners during this period.",
        "HR,SENIOR_HR,ADMIN", 0,
    ),
    (
        "How does an employee transfer between branches or departments?",
        "employee transfer, change branch, move employee department, transfer branch",
        "HR updates the employee's Branch and/or Department fields on their profile in the "
        "Employee Directory. This is an HR-managed change — employees can't transfer "
        "themselves.",
        "HR,SENIOR_HR,ADMIN", 0,
    ),
    (
        "How does a department change work?",
        "change department, department change, move to different department",
        "HR edits the employee's profile and updates the Department field. If the new "
        "department has its own manager, that may also affect approval routing.",
        "HR,SENIOR_HR,ADMIN", 0,
    ),
    (
        "How does a manager (reporting line) change work?",
        "change reporting manager, change manager, reassign manager, update reporting manager",
        "HR edits the employee's profile and updates the Reporting Manager field. This "
        "changes both who approves their leave/permission requests and, for GM reporting "
        "lines, which GM's Directory they appear under.",
        "HR,SENIOR_HR,ADMIN", 0,
    ),
    (
        "What are the employee status types?",
        "employee status types, what is onboarding status, what is active status, "
        "what is exited status",
        "An employee's status is one of: Onboarding (new joiner, not yet fully active), "
        "Active (current employee), or Exited (after their resignation is accepted/exit "
        "processed).",
        "ALL", 0,
    ),

    # ------------------------------------------------------------------
    # 15. Queries / Support (expanded)
    # ------------------------------------------------------------------
    (
        "Who can resolve my query?",
        "who resolves query, who answers my query, who closes query",
        "Whoever you sent it to — your Manager, HR, or Admin, depending on who you picked "
        "when raising it — can reply and mark it Resolved. For HR-directed queries, any HR/"
        "Senior HR user can respond, since that queue is shared.",
        "ALL", 0,
    ),
    (
        "How do I check my query status?",
        "check query status, query status, is my query answered",
        "Go to My Queries (Ask a Query) to see the status of everything you've raised — Open "
        "or Resolved — along with any replies.",
        "ALL", 0,
    ),
    (
        "Can I reopen a resolved query?",
        "reopen query, reopen resolved query, query not actually fixed",
        "Yes. If a query is marked Resolved but the issue isn't actually fixed, you can "
        "reopen it from My Queries so it goes back to Open status.",
        "ALL", 0,
    ),

    # ==================================================================
    # 16. Role-wise chatbot knowledge expansion
    # ==================================================================
    (
        'How do I find a feature in SPS HRMS?',
        'find feature, where is feature, menu, sidebar, navigation, where can i find',
        'Use the sidebar menu to open the relevant module. If you still cannot find a feature, ask the chatbot or raise a query to HR.',
        'ALL', 0,
    ),
    (
        'How do I know which actions I am allowed to perform?',
        'my permissions, what can i do, allowed actions, access permissions',
        'Your available screens and actions depend on your role, reporting hierarchy, department, and the workflow configured for the request.',
        'ALL', 0,
    ),
    (
        "Why can't I see an approval screen?",
        'approval screen missing, cannot see approval, no approval menu, approval access',
        'Approval screens are role-based. If you are not an authorized approver for that workflow, the screen may not be visible. Check with HR if your access looks incorrect.',
        'ALL', 0,
    ),
    (
        "Can I see another employee's personal information?",
        'other employee data, employee personal details, see colleague profile, privacy',
        'Personal employee information is role-restricted. Employees normally see their own details, while HR/Admin and authorized management roles receive broader access according to their responsibilities.',
        'ALL', 0,
    ),
    (
        'What should I do if a page is not loading?',
        'page not loading, screen not loading, page error, module not opening',
        'Refresh the page and try again. If the issue continues, record the page name and error message and raise a query to HR/Admin or your support contact.',
        'ALL', 0,
    ),
    (
        'What should I do if my information looks incorrect?',
        'wrong information, incorrect profile, wrong data, incorrect details',
        'Do not create duplicate records. Raise a query with the exact field, date, and correction required. HR/Admin can verify and update controlled employee information.',
        'ALL', 0,
    ),
    (
        'How do I check whether a request is pending?',
        'pending request, request pending, request status, pending approval',
        'Open the relevant module and review the request status. For personal requests such as leave, WFH, overtime, and increments, the status is shown with the request record.',
        'ALL', 0,
    ),
    (
        'What does Pending mean?',
        'pending meaning, what does pending mean, request pending meaning',
        'Pending means the request has been submitted but the required approval or processing step has not yet been completed.',
        'ALL', 0,
    ),
    (
        'What does Approved mean?',
        'approved meaning, approved request meaning',
        'Approved means the required approver has accepted the request at that workflow stage. Some workflows can have more than one approval stage.',
        'ALL', 0,
    ),
    (
        'What does Rejected mean?',
        'rejected meaning, rejected request meaning',
        'Rejected means the approver or responsible team declined the request. Check the request details for any available reason and contact the relevant approver if clarification is needed.',
        'ALL', 0,
    ),
    (
        'Can I submit the same request twice?',
        'duplicate request, submit twice, duplicate leave request, duplicate wfh',
        'Avoid duplicate submissions. First check the existing request status; if you need to correct it, use the available withdrawal/correction process or contact HR.',
        'ALL', 0,
    ),
    (
        'Where can I see my requests?',
        'my requests, view requests, request history, submitted requests',
        "Use the relevant module's history/status page. Leave and WFH requests are available under My Leaves, while queries and other requests have their own status views.",
        'ALL', 0,
    ),
    (
        'How do I contact HR through the system?',
        'contact hr through system, message hr, ask hr, hr query',
        'Open Ask a Query, select HR as the recipient, enter the issue clearly, and submit it. You can follow the response from the same query area.',
        'ALL', 0,
    ),
    (
        'How do I contact my Manager through the system?',
        'contact manager, message manager, manager query, ask manager',
        'Open Ask a Query, choose your Manager as the recipient, describe the issue, and submit. You can track the query status and reply from My Queries.',
        'ALL', 0,
    ),
    (
        'What is the difference between HR and Manager queries?',
        'hr vs manager query, manager query hr query, who should i ask',
        'Use a Manager query for team/reporting or operational matters and an HR query for HR, employee records, payroll, attendance corrections, and policy matters.',
        'ALL', 0,
    ),
    (
        'What is a reporting manager?',
        'reporting manager meaning, reporting line, who is reporting manager',
        'A reporting manager is the manager assigned to an employee in the HRMS. The reporting relationship can affect approvals and management visibility.',
        'ALL', 0,
    ),
    (
        'Why does my approval go to a different person?',
        'wrong approver, approval routed differently, approver changed, request goes to another person',
        'Approval routing can depend on department, reporting manager, role, and workflow rules. Software employees may route certain requests to the CTO. If routing is incorrect, contact HR.',
        'ALL', 0,
    ),
    (
        'What is a project assignment?',
        'project assignment meaning, assignment meaning, project member assignment',
        'A project assignment links an employee to a project, records their role on that project, and can be used for production tracking and project visibility.',
        'ALL', 0,
    ),
    (
        'Can an employee have no project assignment?',
        'no project assignment, employee without project, unassigned employee',
        "Yes. Project assignment is separate from the employee's reporting relationship. An employee can remain under their reporting hierarchy without a current project assignment.",
        'ALL', 0,
    ),
    (
        'What can I do as an Employee?',
        'employee access, employee dashboard, employee permissions, employee features',
        'Employees can manage their own profile information where permitted, view personal attendance, leaves, payslips, projects and tracker information when applicable, submit requests, and raise queries.',
        'EMPLOYEE', 0,
    ),
    (
        'Can I approve my own leave?',
        'employee approve own leave, approve own request',
        'No. You can submit and track your own request, but approval is handled by the configured approver.',
        'EMPLOYEE', 0,
    ),
    (
        "Can I approve my teammate's leave?",
        'employee approve teammate leave, approve colleague leave',
        'No. Employee access does not provide team leave approval. Authorized managers/approvers handle team requests.',
        'EMPLOYEE', 0,
    ),
    (
        'Can I edit my department myself?',
        'employee change department, change my department',
        'No. Department changes are HR-managed. Raise a query to HR if your department is incorrect.',
        'EMPLOYEE', 0,
    ),
    (
        'Can I change my reporting manager myself?',
        'employee change manager, change reporting manager myself',
        'No. Reporting-manager changes are controlled by HR/Admin. Ask HR if the displayed manager is incorrect.',
        'EMPLOYEE', 0,
    ),
    (
        'Can I assign myself to a project?',
        'employee assign self project, join project myself',
        'No. Project assignments are created by authorized project/HR users. Ask your PM, Lead, or HR if you need a project assignment.',
        'EMPLOYEE', 0,
    ),
    (
        'Can I see my current project assignments?',
        'my project assignments, projects assigned to me, current projects',
        "Yes. Your Projects area shows the projects linked to you, subject to your account's access.",
        'EMPLOYEE', 0,
    ),
    (
        'Can I work on more than one project?',
        'employee multiple projects, two projects, multiple assignments',
        'Yes, the system supports multiple current project assignments when they are assigned to you.',
        'EMPLOYEE', 0,
    ),
    (
        'Can I enter production for a project I am not assigned to?',
        'production unassigned project, tracker other project',
        'Production entries should be tied to the project you are working on and assigned to. Contact your PM/Lead if the required project is missing.',
        'EMPLOYEE', 0,
    ),
    (
        'What if my project target is wrong?',
        'wrong daily target, incorrect target, project target wrong',
        "Do not change records outside your permissions. Ask your PM/Lead to verify the project's daily target.",
        'EMPLOYEE', 0,
    ),
    (
        'Can I edit an attendance punch?',
        'edit punch, change punch time, employee edit punch',
        'Employees cannot directly edit attendance records. Raise a query to HR for corrections.',
        'EMPLOYEE', 0,
    ),
    (
        'What if I forgot to punch out?',
        'forgot punch out, missed punch out, forgot checkout',
        'Raise an attendance correction query to HR with the date and approximate correct out time.',
        'EMPLOYEE', 0,
    ),
    (
        'Can I download my payslip?',
        'employee download payslip, payslip download',
        'Yes. Open Payslips and select the processed month you want to view or download.',
        'EMPLOYEE', 0,
    ),
    (
        "Can I see another employee's payslip?",
        'see colleague payslip, other employee salary, employee payslip access',
        'No. Payslip information is personal and role-restricted.',
        'EMPLOYEE', 0,
    ),
    (
        'Can I see my leave history?',
        'my leave history, employee leave history, past leaves',
        'Yes. My Leaves contains your submitted leave, permission, and WFH request history and statuses.',
        'EMPLOYEE', 0,
    ),
    (
        'Can I cancel a pending leave?',
        'employee cancel pending leave, withdraw pending leave',
        'If the request shows a Cancel/Withdraw option, use it. If no option is available, contact HR.',
        'EMPLOYEE', 0,
    ),
    (
        'Can I submit WFH through SPS HRMS?',
        'employee apply wfh, submit wfh, work from home request',
        'Yes. Use My Leaves and select the WFH request option, then provide the required dates and reason.',
        'EMPLOYEE', 0,
    ),
    (
        'Can I submit overtime permission?',
        'employee overtime request, submit overtime, sunday permission',
        'Use the Overtime/Sunday Permission workflow when available and provide the required date and permission details before working overtime.',
        'EMPLOYEE', 0,
    ),
    (
        'How do I check my latest increment?',
        'employee increment history, latest increment, increment details',
        'Use your increment information/status area or ask the chatbot for your latest increment status. Personal increment data is retrieved dynamically.',
        'EMPLOYEE', 0,
    ),
    (
        'How do I check my resignation status?',
        'employee resignation status, resignation request, exit status',
        'Use the resignation/employee exit area or ask the chatbot for your latest resignation status. The chatbot can retrieve your latest recorded request.',
        'EMPLOYEE', 0,
    ),
    (
        'What can a Manager manage?',
        'manager access, manager permissions, manager dashboard',
        'A Manager can handle authorized team workflows such as team leave approvals, attendance/team visibility, queries, and other management functions provided by their reporting hierarchy.',
        'MANAGER', 0,
    ),
    (
        'Can a Manager approve team leave?',
        'manager approve leave, team leave approval, manager leave approval',
        'Yes. A reporting Manager can approve eligible leave, permission, and WFH requests routed to them.',
        'MANAGER', 0,
    ),
    (
        'Can a Manager approve their own leave?',
        'manager approve own leave, manager self approval',
        "No. A Manager's own request is routed according to their reporting hierarchy rather than being self-approved.",
        'MANAGER', 0,
    ),
    (
        'Can a Manager see team leave status?',
        'manager team leave status, view team leave',
        'Yes. Authorized managers can view the leave status of employees in their reporting scope.',
        'MANAGER', 0,
    ),
    (
        'Can a Manager see employees outside their team?',
        'manager cross team visibility, other team employees',
        'Visibility is role and hierarchy based. A Manager should normally work with the employees in their authorized reporting scope.',
        'MANAGER', 0,
    ),
    (
        'Can a Manager correct employee attendance?',
        'manager edit attendance, manager attendance correction',
        'Attendance correction is primarily handled by HR. A Manager should raise or route the issue to HR when a correction is needed.',
        'MANAGER', 0,
    ),
    (
        'Can a Manager view team production?',
        'manager team production, manager tracker, team output',
        'Authorized Managers can review production/tracker information for employees within their scope where the project/workflow exposes it.',
        'MANAGER', 0,
    ),
    (
        'Can a Manager change a project target?',
        'manager project target, change daily target manager',
        'Project target changes should be handled by the authorized project owner/PM or other configured role. Contact the PM/HR if the target needs correction.',
        'MANAGER', 0,
    ),
    (
        'Can a Manager assign employees to projects?',
        'manager assign project, manager project assignment',
        'Project assignment permissions depend on the project and role configuration. Project Managers, Leads, and HR/Admin are the primary assignment roles in the current workflow.',
        'MANAGER', 0,
    ),
    (
        'Can a Manager approve allowances?',
        'manager allowance approval, manager approve allowance',
        'Allowance routing depends on department. Production allowance requests route to the Project Manager first, while other requests go to HR.',
        'MANAGER', 0,
    ),
    (
        'Can a Manager provide appraisal feedback?',
        'manager appraisal feedback, manager performance feedback',
        'Yes, when an appraisal or increment workflow requests feedback from the reporting manager.',
        'MANAGER', 0,
    ),
    (
        'Can a Manager initiate an increment?',
        'manager initiate increment, manager create increment',
        'Increment initiation is handled through the configured HR workflow. HR initiates increment requests and routes them for manager/GM feedback.',
        'MANAGER', 0,
    ),
    (
        'Can a Manager view employee salary?',
        'manager salary access, manager payslip, team salary',
        'Salary information is role-restricted. Managers should only see salary information where the application explicitly grants it.',
        'MANAGER', 0,
    ),
    (
        'How does a Manager handle a new joiner?',
        'manager new joiner, manager onboarding, onboarding feedback',
        'Managers can provide onboarding/training feedback for new joiners assigned to their reporting/project scope.',
        'MANAGER', 0,
    ),
    (
        'Can a Manager raise a query to HR?',
        'manager ask hr, manager hr query',
        'Yes. Use the query workflow to raise an HR-related issue and track the response.',
        'MANAGER', 0,
    ),
    (
        'Can a Manager resolve employee queries?',
        'manager resolve query, team query, manager employee query',
        'A Manager can reply to and resolve queries routed to them, subject to the query workflow.',
        'MANAGER', 0,
    ),
    (
        'Can a Manager see project assignments?',
        'manager project assignments, team project assignment',
        'Authorized management users can view project assignment information within their access scope.',
        'MANAGER', 0,
    ),
    (
        'What should a Manager do if an employee is missing from the team?',
        'employee missing team, missing employee manager',
        "Ask HR to verify the employee's reporting manager, department, branch, and active status. These fields determine management visibility.",
        'MANAGER', 0,
    ),
    (
        'Can a Manager see employee attendance history?',
        'manager attendance history, team attendance history',
        'Authorized managers can view attendance information for employees within their permitted scope where the dashboard provides it.',
        'MANAGER', 0,
    ),
    (
        'What if a team leave request is stuck pending?',
        'manager pending leave, leave stuck pending, pending approval team',
        'Open the team leave status/approval area and verify the current approver stage. If routing is incorrect, contact HR.',
        'MANAGER', 0,
    ),
    (
        'Can a PM approve team leave?',
        'pm approve leave, project manager leave approval',
        'Yes. Team leave/permission/WFH requests routed to the PM can be reviewed and approved according to the workflow.',
        'PROJECT_MANAGER', 0,
    ),
    (
        'Can a PM approve their own leave?',
        'pm self approve leave, project manager own leave',
        "No. A PM's own leave is routed to their reporting GM or CTO for Software-department PMs.",
        'PROJECT_MANAGER', 0,
    ),
    (
        'Can a PM view team leave status?',
        'pm team leave, pm leave status, project team leave',
        'Yes. A PM can review leave requests for the team within their authorized project/reporting scope.',
        'PROJECT_MANAGER', 0,
    ),
    (
        'Can a PM view daily production?',
        'pm daily production, pm team production, pm tracker dashboard',
        'Yes. The PM can review team daily tracker/production information for assigned project teams.',
        'PROJECT_MANAGER', 0,
    ),
    (
        'Can a PM set the daily target?',
        'pm set daily target, project target pm, change project target',
        "Yes. The current FAQ workflow identifies the PM as an authorized role to set or adjust a project's daily target.",
        'PROJECT_MANAGER', 0,
    ),
    (
        'Can a PM modify employee tracker entries?',
        'pm edit tracker, pm modify production entry',
        "PMs can review or correct tracker entries for their team where the application's edit permissions allow it.",
        'PROJECT_MANAGER', 0,
    ),
    (
        'Can a PM assign an employee to a project?',
        'pm assign employee project, project assignment pm',
        'Yes. Project assignment is one of the roles supported for PM/Lead workflows, subject to project access.',
        'PROJECT_MANAGER', 0,
    ),
    (
        'Can a PM remove an employee from a project?',
        'pm remove employee project, end project assignment',
        'A project assignment can be made no longer current when an employee moves off a project. Use the project assignment workflow available to the PM.',
        'PROJECT_MANAGER', 0,
    ),
    (
        'Can a PM manage allowances?',
        'pm allowance approval, project allowance request',
        'For Production employees, allowance requests route to the PM first and then HR for final approval.',
        'PROJECT_MANAGER', 0,
    ),
    (
        'Can a PM initiate their own increment?',
        'pm own increment, project manager own increment',
        "No. HR initiates a PM increment request. The PM's GM provides the performance feedback.",
        'PROJECT_MANAGER', 0,
    ),
    (
        'What does a PM do in an increment review?',
        'pm increment feedback, pm increment review',
        'The PM provides performance information for their team when requested and supports HR/GM with relevant work records.',
        'PROJECT_MANAGER', 0,
    ),
    (
        'What does a PM do in appraisal?',
        'pm appraisal role, project manager appraisal',
        'The PM reviews project/team production and QA performance and provides input for appraisal scoring when requested.',
        'PROJECT_MANAGER', 0,
    ),
    (
        'Can a PM approve overtime for team members?',
        'pm overtime approval, team overtime pm',
        'PMs can review overtime/Sunday permission requests routed to them according to the configured workflow.',
        'PROJECT_MANAGER', 0,
    ),
    (
        'Can a PM provide onboarding feedback?',
        'pm onboarding feedback, new joiner pm feedback',
        'Yes. PMs can provide onboarding/training feedback for new joiners in their team/project scope.',
        'PROJECT_MANAGER', 0,
    ),
    (
        'Can a PM see all company employees?',
        'pm all employees, project manager company access',
        'No. PM visibility is primarily based on assigned projects and authorized team scope.',
        'PROJECT_MANAGER', 0,
    ),
    (
        "Can a PM manage another PM's project?",
        'pm manage another project, cross project pm',
        "Only if that project is included in the PM's authorized project scope. Project access is not automatically global.",
        'PROJECT_MANAGER', 0,
    ),
    (
        'What should a PM do if a project target is missing?',
        'pm target missing, project target not showing',
        'Verify that the project is active and the PM has access. If the target is still missing, contact HR/Admin or the responsible project configuration user.',
        'PROJECT_MANAGER', 0,
    ),
    (
        'What should a PM do if a team member is missing from the project?',
        'pm missing team member, project member missing',
        "Check the current Project Assignment. If there is no active assignment, use the assignment workflow or ask HR/Admin to correct the employee's project mapping.",
        'PROJECT_MANAGER', 0,
    ),
    (
        'What can a General Manager manage?',
        'gm access, general manager permissions, gm dashboard',
        'A General Manager manages employees and Project Managers within their reporting hierarchy, including eligible leave approvals, visibility, and feedback workflows.',
        'GENERAL_MANAGER', 0,
    ),
    (
        'Can a GM manage direct employees?',
        'gm direct employees, gm manage employees',
        "Yes. Employees can report directly to a GM; the GM's team is not limited to PMs.",
        'GENERAL_MANAGER', 0,
    ),
    (
        "Can a GM see another GM's team?",
        'gm other team, cross gm employees, other gm directory',
        "No. GM visibility is bounded by the reporting hierarchy and should not expose another GM's team.",
        'GENERAL_MANAGER', 0,
    ),
    (
        'Can a GM approve their own leave?',
        'gm own leave approval, self approve gm leave',
        "No. A GM's own request follows the next configured approval level rather than self-approval.",
        'GENERAL_MANAGER', 0,
    ),
    (
        'Can a GM view team leave status?',
        'gm team leave status, gm leave dashboard',
        'Yes. A GM can see leave status for PMs and employees within their reporting hierarchy.',
        'GENERAL_MANAGER', 0,
    ),
    (
        'Can a GM approve allowances?',
        'gm allowance approval, gm manage allowance',
        'No. Allowance approval belongs to HR and, for Production employees, their PM first.',
        'GENERAL_MANAGER', 0,
    ),
    (
        'Does a GM initiate a PM increment?',
        'gm initiate pm increment, gm start increment',
        'No. HR initiates the PM increment request; the GM provides the performance feedback.',
        'GENERAL_MANAGER', 0,
    ),
    (
        'What does a GM submit for a PM increment?',
        'gm pm increment feedback, gm increment records',
        'The GM reviews PM performance and submits feedback with relevant work records or supporting documentation to HR.',
        'GENERAL_MANAGER', 0,
    ),
    (
        'Can a GM see PM performance?',
        'gm pm performance, gm project manager performance',
        'Yes, within the reporting hierarchy and available dashboard permissions, a GM can review PM performance information.',
        'GENERAL_MANAGER', 0,
    ),
    (
        'What is the GM role in appraisal?',
        'gm appraisal role, general manager appraisal',
        'A GM reviews the performance of PMs and other staff in the hierarchy and provides feedback used in appraisal/increment preparation.',
        'GENERAL_MANAGER', 0,
    ),
    (
        'Can a GM edit employee salary?',
        'gm salary edit, gm change salary',
        'Salary changes are controlled through HR/increment workflows. A GM provides feedback rather than directly applying salary changes.',
        'GENERAL_MANAGER', 0,
    ),
    (
        'Can a GM manage employee department?',
        'gm change department, gm department edit',
        'Department and reporting-line changes are HR-managed. The GM should request the change through HR.',
        'GENERAL_MANAGER', 0,
    ),
    (
        'Can a GM assign employees to projects?',
        'gm assign project, gm project assignment',
        'Project assignment is handled by authorized project/HR roles. A GM can oversee the reporting hierarchy but does not automatically receive project-assignment rights.',
        'GENERAL_MANAGER', 0,
    ),
    (
        'What should a GM do if a PM is missing?',
        'gm pm missing, project manager missing gm',
        "Ask HR to verify the PM's reporting manager field and active status. The GM hierarchy depends on those employee relationships.",
        'GENERAL_MANAGER', 0,
    ),
    (
        'Can a GM resolve employee queries?',
        'gm resolve query, gm employee query',
        'A GM can handle queries routed to them according to the query workflow and their reporting scope.',
        'GENERAL_MANAGER', 0,
    ),
    (
        'Can a GM approve employee overtime?',
        'gm overtime approval, gm sunday permission',
        "Approval depends on the employee's routing and workflow. Review the request in the approval area if it is routed to the GM.",
        'GENERAL_MANAGER', 0,
    ),
    (
        'What happens after a GM submits increment feedback?',
        'gm feedback submitted, after gm increment feedback',
        "HR reviews the GM's feedback and supporting records, then forwards the proposal to the COO for final approval.",
        'GENERAL_MANAGER', 0,
    ),
    (
        'What can HR manage?',
        'hr permissions, hr access, hr dashboard',
        'HR manages employee records, attendance corrections, leave/request workflows, payroll-related operations, increments, queries, onboarding/offboarding, and other HR administration available in the system.',
        'HR,SENIOR_HR', 0,
    ),
    (
        'Can HR edit employee profiles?',
        'hr edit employee profile, hr employee details',
        'Yes. HR can update employee records such as profile details, branch, department, reporting manager, and status where permitted.',
        'HR,SENIOR_HR,ADMIN', 0,
    ),
    (
        "Can HR change an employee's department?",
        'hr change department, hr transfer department',
        "Yes. HR can update the employee's Department field and verify any approval-routing impact.",
        'HR,SENIOR_HR,ADMIN', 0,
    ),
    (
        "Can HR change an employee's reporting manager?",
        'hr change reporting manager, hr manager reassignment',
        'Yes. HR can update the Reporting Manager field. This can change approval routing and management visibility.',
        'HR,SENIOR_HR,ADMIN', 0,
    ),
    (
        "Can HR change an employee's branch?",
        'hr change branch, hr branch transfer',
        'Yes. Branch changes are managed through the employee profile by authorized HR/Admin users.',
        'HR,SENIOR_HR,ADMIN', 0,
    ),
    (
        'Can HR onboard an employee?',
        'hr onboard employee, hr new joiner',
        'Yes. HR can onboard a new employee with basic details, branch, department, and reporting manager.',
        'HR,SENIOR_HR,ADMIN', 0,
    ),
    (
        'What statuses can HR assign to employees?',
        'hr employee statuses, onboarding active exited',
        'The system uses statuses such as Onboarding, Active, and Exited. HR manages these lifecycle changes.',
        'HR,SENIOR_HR,ADMIN', 0,
    ),
    (
        'Can HR correct attendance?',
        'hr attendance correction, fix employee attendance',
        'Yes. Attendance corrections are handled by HR when employees report missing or incorrect punches.',
        'HR,SENIOR_HR,ADMIN', 0,
    ),
    (
        'Can HR view attendance records?',
        'hr attendance records, hr employee attendance',
        'Yes. HR has access to attendance information needed for HR operations and payroll processing.',
        'HR,SENIOR_HR,ADMIN', 0,
    ),
    (
        'Can HR approve leave?',
        'hr approve leave, hr leave approval',
        'HR can approve or process leave requests where the configured workflow routes them to HR, including certain final or secondary approval stages.',
        'HR,SENIOR_HR,ADMIN', 0,
    ),
    (
        'Can HR see all employee leave requests?',
        'hr all leave, hr leave requests',
        'HR has broad visibility into leave workflows needed for HR operations, subject to the configured permissions.',
        'HR,SENIOR_HR,ADMIN', 0,
    ),
    (
        'Can HR process payroll?',
        'hr payroll process, hr salary processing',
        'Yes. HR manages the monthly payroll workflow and reviews attendance/corrections and salary components before processing.',
        'HR,SENIOR_HR,ADMIN', 0,
    ),
    (
        'Can HR manage allowances?',
        'hr allowance requests, hr allowance approval',
        'Yes. HR handles allowance requests and final approval where the workflow requires HR.',
        'HR,SENIOR_HR,ADMIN', 0,
    ),
    (
        'Who initiates an employee increment?',
        'hr initiate increment, create increment request',
        'HR initiates increment requests and routes them through the configured manager/GM feedback and COO approval process.',
        'HR,SENIOR_HR', 0,
    ),
    (
        'Can HR initiate a PM increment?',
        'hr pm increment, start pm increment',
        "Yes. HR initiates a PM's increment request and routes it to the PM's reporting GM for feedback.",
        'HR,SENIOR_HR', 0,
    ),
    (
        'What does HR do after GM feedback?',
        'hr after gm feedback, hr review increment',
        'HR reviews the GM feedback and supporting records, then forwards the increment proposal to the COO for final approval.',
        'HR,SENIOR_HR', 0,
    ),
    (
        'Can HR run appraisals?',
        'hr appraisal calculator, hr appraisal',
        'Yes. HR runs the appraisal calculation, reviews manual scoring inputs, and prepares/export reports according to the appraisal workflow.',
        'HR,SENIOR_HR,ADMIN', 0,
    ),
    (
        'Can HR manage employee queries?',
        'hr queries, hr resolve employee query',
        'Yes. HR/Senior HR can respond to and resolve HR-directed employee queries.',
        'HR,SENIOR_HR,ADMIN', 0,
    ),
    (
        'Can HR manage employee exits?',
        'hr resignation, hr employee exit, offboarding',
        'Yes. HR manages resignation/exit processing and employee lifecycle status changes.',
        'HR,SENIOR_HR,ADMIN', 0,
    ),
    (
        'Can HR see employees without projects?',
        'hr unassigned employees, employee no project hr',
        'Yes. HR can view and manage employee records independently of project assignment.',
        'HR,SENIOR_HR,ADMIN', 0,
    ),
    (
        'Can HR assign employees to projects?',
        'hr project assignment, hr assign project',
        'Yes. HR/Admin can create or update project assignments as part of employee/project administration.',
        'HR,SENIOR_HR,ADMIN', 0,
    ),
    (
        'Can HR manage reporting hierarchy?',
        'hr hierarchy, hr reporting hierarchy, employee manager hierarchy',
        'Yes. HR maintains reporting manager relationships that drive hierarchy and approval routing.',
        'HR,SENIOR_HR,ADMIN', 0,
    ),
    (
        'Can Senior HR perform HR operations?',
        'senior hr permissions, gm hr access, senior hr role',
        'Senior HR has HR-level operational access for the areas granted by the system, including employee and HR workflow management.',
        'SENIOR_HR', 0,
    ),
    (
        'What is the difference between HR and Senior HR?',
        'hr vs senior hr, senior hr difference',
        'Both are HR roles with broad HR operational access. The exact screens and approval authority depend on the role permissions configured in the system.',
        'HR,SENIOR_HR', 0,
    ),
    (
        'What can the CTO manage?',
        'cto access, cto permissions, cto dashboard',
        'The CTO handles Software-department management and approval workflows assigned to the CTO, including Software employee requests and relevant team oversight.',
        'CTO', 0,
    ),
    (
        'Who approves Software employee leave?',
        'software leave approver, cto leave approval, software employee leave',
        'Software-department leave, permission, and WFH requests can route to the CTO instead of the normal reporting manager workflow.',
        'CTO', 0,
    ),
    (
        'Can the CTO approve Software team leave?',
        'cto approve software leave, cto team leave',
        'Yes. The CTO can review eligible Software-department requests routed to the CTO.',
        'CTO', 0,
    ),
    (
        'Can the CTO approve their own leave?',
        'cto own leave, cto self approve',
        "No. The CTO's own request should follow the configured higher-level approval workflow.",
        'CTO', 0,
    ),
    (
        'Can the CTO see Software employees?',
        'cto software team, cto employee visibility',
        'Yes. CTO access is designed to cover Software-department management and related workflows.',
        'CTO', 0,
    ),
    (
        'Can the CTO see Production employees?',
        'cto production employees, cto other departments',
        'Access is role and department based. CTO responsibilities are primarily tied to Software, so unrelated department data may not be available.',
        'CTO', 0,
    ),
    (
        'Can the CTO view Software attendance?',
        'cto software attendance, software team attendance',
        'Authorized CTO dashboards can provide attendance visibility for the Software team where the feature is exposed.',
        'CTO', 0,
    ),
    (
        'Can the CTO view Software production?',
        'cto software production, software tracker cto',
        'Where Software production/tracker data is configured, the CTO can review the relevant team information within the available dashboard.',
        'CTO', 0,
    ),
    (
        'Can the CTO manage project assignments?',
        'cto project assignment, cto assign software project',
        'Project assignment rights depend on the project and role configuration. CTO access does not automatically make every project editable.',
        'CTO', 0,
    ),
    (
        'Can the CTO change employee department?',
        'cto change department, cto employee department',
        'Department changes are HR-managed. The CTO should request such changes through HR.',
        'CTO', 0,
    ),
    (
        'Can the CTO change reporting managers?',
        'cto reporting manager change, cto change manager',
        'Reporting-manager changes are controlled by HR/Admin. The CTO can raise the requirement to HR.',
        'CTO', 0,
    ),
    (
        'Can the CTO approve increments?',
        'cto increment approval, cto salary increment',
        'Increment approval is normally finalized by the COO after HR review. CTO feedback may be part of Software management workflows where configured.',
        'CTO', 0,
    ),
    (
        'Can the CTO provide appraisal feedback?',
        'cto appraisal feedback, software appraisal cto',
        'Yes, when the appraisal workflow requests feedback from the CTO for Software employees.',
        'CTO', 0,
    ),
    (
        'Can the CTO resolve employee queries?',
        'cto employee query, cto resolve query',
        'The CTO can handle queries routed to the CTO according to the query workflow.',
        'CTO', 0,
    ),
    (
        'Can the CTO approve overtime?',
        'cto overtime, software overtime approval',
        "Overtime approval depends on the employee's workflow. Software requests may route to the CTO where configured.",
        'CTO', 0,
    ),
    (
        'What should the CTO do if a Software employee is missing?',
        'cto missing employee, software employee missing',
        "Ask HR to verify the employee's department, reporting manager, active status, and role. These fields affect Software-team visibility and routing.",
        'CTO', 0,
    ),
    (
        "Can the CTO see another department's hierarchy?",
        'cto cross department, cto other hierarchy',
        "Visibility is role-based and should remain within the CTO's authorized management scope.",
        'CTO', 0,
    ),
    (
        'Why does Software leave route to CTO?',
        'why software leave cto, software approval routing',
        'The HRMS contains a Software-department routing rule that sends eligible leave/permission/WFH requests to the CTO.',
        'CTO', 0,
    ),
    (
        'Can the CTO manage payroll?',
        'cto payroll, cto salary processing',
        'Payroll operations are primarily handled by HR. The CTO should use HR workflows for payroll changes or corrections.',
        'CTO', 0,
    ),
    (
        'Can the CTO onboard employees?',
        'cto onboarding, cto new joiner',
        'HR owns employee onboarding. The CTO may provide onboarding/training feedback for Software team members when requested.',
        'CTO', 0,
    ),
    (
        'What can the COO manage?',
        'coo access, coo permissions, coo dashboard',
        'The COO has company-level management access for major approval and oversight workflows, including increment approvals and broad organizational metrics available in the dashboard.',
        'COO', 0,
    ),
    (
        'What does the COO approve?',
        'coo approval, final approval coo, coo approvals',
        'The COO provides final approval for increment/salary proposals after HR and management feedback. Other requests can reach the COO when the configured workflow requires it.',
        'COO', 0,
    ),
    (
        'Can the COO approve increments?',
        'coo increment approval, approve increment coo',
        'Yes. The COO gives final approval on increment proposals after HR review.',
        'COO', 0,
    ),
    (
        'What happens before an increment reaches the COO?',
        'before coo increment, increment pre approval',
        'HR initiates and reviews the request, manager/GM feedback is collected where required, and HR forwards the proposal to the COO for final approval.',
        'COO', 0,
    ),
    (
        'Can the COO view company employee counts?',
        'coo employee count, company employees coo',
        'The COO dashboard provides broad organizational information, including employee statistics available to the role.',
        'COO', 0,
    ),
    (
        'Can the COO view branch performance?',
        'coo branch performance, branch metrics coo',
        'Yes, where branch-level management metrics are exposed in the COO dashboard.',
        'COO', 0,
    ),
    (
        'Can the COO view attendance metrics?',
        'coo attendance, company attendance coo',
        'The COO can access broad attendance/management metrics available through the COO dashboard.',
        'COO', 0,
    ),
    (
        'Can the COO view leave information?',
        'coo leave, company leave coo',
        'The COO can review leave information and approval workflows that are routed to or visible at COO level.',
        'COO', 0,
    ),
    (
        'Can the COO manage employee profiles?',
        'coo employee profile, coo edit employee',
        'Employee profile administration is primarily an HR/Admin responsibility. The COO should request HR to make controlled employee-record changes.',
        'COO', 0,
    ),
    (
        'Can the COO change reporting managers?',
        'coo change manager, coo hierarchy edit',
        'Reporting hierarchy changes are maintained by HR/Admin. The COO can request changes through HR.',
        'COO', 0,
    ),
    (
        'Can the COO assign projects?',
        'coo project assignment, coo assign project',
        'Project assignment is handled by project/HR roles. COO oversight does not automatically grant project-edit permissions.',
        'COO', 0,
    ),
    (
        'Can the COO view production metrics?',
        'coo production, company production, production metrics coo',
        'The COO can review organization-level production/project metrics that are available through the dashboard.',
        'COO', 0,
    ),
    (
        'Can the COO review appraisal information?',
        'coo appraisal, appraisal coo',
        'The COO can review appraisal/increment information relevant to final approval and organizational oversight.',
        'COO', 0,
    ),
    (
        'Can the COO approve PM leave?',
        'coo pm leave, coo leave approval',
        'PM leave normally routes to the GM first; certain workflows may require COO sign-off, which appears in the COO approval area.',
        'COO', 0,
    ),
    (
        'Can the COO handle employee queries?',
        'coo employee query, query to coo',
        'Queries should normally be routed to the Manager or HR. The COO can handle queries only when they are specifically routed to the COO.',
        'COO', 0,
    ),
    (
        'Can the COO manage payroll directly?',
        'coo payroll, coo salary processing',
        'Payroll processing is an HR operation. The COO participates in final approval workflows such as increments rather than routine payroll entry.',
        'COO', 0,
    ),
    (
        'Can the COO see all GM teams?',
        'coo gm teams, coo hierarchy visibility',
        "COO-level organizational oversight can provide broader hierarchy visibility than a GM, subject to the dashboard's configured access.",
        'COO', 0,
    ),
    (
        "What is the COO's role in salary changes?",
        'coo salary change, coo salary approval',
        'The COO provides final approval for increment-linked salary changes after HR and management review.',
        'COO', 0,
    ),
    (
        "Can the COO approve an employee's normal leave directly?",
        'coo normal leave, coo approve employee leave',
        "Only if that request is routed to the COO by the configured workflow. Routine leave usually follows the employee's reporting chain.",
        'COO', 0,
    ),
    (
        'What should the COO do if an approval is missing?',
        'coo approval missing, missing coo request',
        "Check the approval queue and request status. If the request did not route correctly, ask HR to verify the employee's reporting and workflow configuration.",
        'COO', 0,
    ),
    (
        'What can an Admin manage?',
        'admin access, admin permissions, administrator dashboard',
        'Admin has the broadest system-management access and can manage users, employee records, roles, configuration, and other administrative functions exposed by the application.',
        'ADMIN', 0,
    ),
    (
        'Can Admin manage user roles?',
        'admin change role, user role admin, assign role',
        'Yes. Admin can manage user accounts and role assignments where the administration screen permits it.',
        'ADMIN', 0,
    ),
    (
        'Can Admin manage employee records?',
        'admin employee records, admin edit employee',
        'Yes. Admin has broad employee-record administration access.',
        'ADMIN', 0,
    ),
    (
        'Can Admin change department?',
        'admin change department, admin department',
        'Yes. Admin can update employee organizational fields where permitted.',
        'ADMIN', 0,
    ),
    (
        'Can Admin change reporting manager?',
        'admin change manager, admin reporting manager',
        'Yes. Admin can maintain reporting-manager relationships where the administration workflow allows it.',
        'ADMIN', 0,
    ),
    (
        'Can Admin change employee status?',
        'admin employee status, admin activate employee, admin exit employee',
        'Yes. Admin can manage employee lifecycle/status fields where the administration workflow exposes them.',
        'ADMIN', 0,
    ),
    (
        'Can Admin onboard an employee?',
        'admin onboard employee, admin new employee',
        'Yes. Admin has broad administrative access and can support employee onboarding workflows.',
        'ADMIN', 0,
    ),
    (
        'Can Admin assign projects?',
        'admin project assignment, admin assign employee project',
        'Yes. Admin can manage project assignments as part of system administration.',
        'ADMIN', 0,
    ),
    (
        'Can Admin see all employees?',
        'admin all employees, administrator employee visibility',
        "Admin has broad employee visibility for system administration, subject to the application's permission configuration.",
        'ADMIN', 0,
    ),
    (
        'Can Admin view salary information?',
        'admin salary access, admin payslip, admin salary',
        'Admin has broad administrative access to system data, while salary actions should still follow the HR/payroll workflow.',
        'ADMIN', 0,
    ),
    (
        'Can Admin correct attendance?',
        'admin attendance correction, admin fix attendance',
        'Admin has broad access, but attendance corrections should follow the HR-controlled correction process to preserve auditability.',
        'ADMIN', 0,
    ),
    (
        'Can Admin approve leave?',
        'admin leave approval, admin approve request',
        'Admin may have broad access, but leave approval should follow the configured reporting/HR workflow rather than bypassing the normal approver chain.',
        'ADMIN', 0,
    ),
    (
        'Can Admin manage chatbot FAQs?',
        'admin chatbot faq, manage faq, edit chatbot knowledge',
        'If the application exposes FAQ administration, Admin can manage chatbot knowledge records. The load_faqs management command is also used to bulk load static FAQs.',
        'ADMIN', 0,
    ),
    (
        'How do I reload the chatbot FAQ knowledge base?',
        'reload faq, load faqs, chatbot faq command, update chatbot knowledge',
        'Run the Django management command `python manage.py load_faqs`. It is safe to re-run because entries are matched by question and updated in place.',
        'ADMIN', 0,
    ),
    (
        'Does loading FAQs create duplicates?',
        'faq duplicates, load faqs duplicate, duplicate chatbot faq',
        'The FAQ loader uses update_or_create with the question as the match key, so re-running it updates existing questions instead of creating duplicate records.',
        'ADMIN', 0,
    ),
    (
        'Can Admin manage branches?',
        'admin branches, branch management admin',
        'Admin can manage organizational configuration such as branches when the corresponding administration screens are available.',
        'ADMIN', 0,
    ),
    (
        'Can Admin manage departments?',
        'admin departments, department management admin',
        'Admin can manage organizational configuration such as departments when the corresponding administration screens are available.',
        'ADMIN', 0,
    ),
    (
        'Can Admin view system queries?',
        'admin employee queries, admin query management',
        'Admin can access query information where the system grants administrative visibility and can assist with unresolved issues.',
        'ADMIN', 0,
    ),
    (
        'Can Admin manage payroll?',
        'admin payroll, administrator payroll',
        'Admin has broad system access, but payroll processing should remain within the HR/payroll workflow and controls.',
        'ADMIN', 0,
    ),
    (
        'Can Admin manage project targets?',
        'admin project target, admin daily target',
        'Admin can support project configuration where the project-management screens grant access; operational target changes should be coordinated with the responsible PM.',
        'ADMIN', 0,
    ),
]


class Command(BaseCommand):
    help = "Loads/updates the SPS Assist starter FAQ knowledge base (safe to re-run)."

    def handle(self, *args, **options):
        created, updated = 0, 0
        for question, keywords, answer, target_roles, priority in FAQS:
            obj, was_created = FAQ.objects.update_or_create(
                question=question,
                defaults={
                    "keywords": keywords,
                    "answer": answer,
                    "target_roles": target_roles,
                    "priority": priority,
                    "is_active": True,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(self.style.SUCCESS(
            f"FAQs loaded: {created} created, {updated} updated (total {len(FAQS)})."
        ))
