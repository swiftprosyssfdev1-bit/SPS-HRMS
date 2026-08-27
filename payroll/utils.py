from decimal import Decimal
from django.db.models import Sum
from attendance.models import AttendanceRecord
from payroll.models import SalaryStructure, Payslip
from attendance.utils import compute_authorized_hours_for_range, build_payroll_cycle_summary

STANDARD_DAILY_HOURS = Decimal('8')

_ONES = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine',
         'Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen',
         'Seventeen', 'Eighteen', 'Nineteen']
_TENS = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety']


def _two_digit_words(n):
    if n < 20:
        return _ONES[n]
    return (_TENS[n // 10] + (' ' + _ONES[n % 10] if n % 10 else '')).strip()


def _three_digit_words(n):
    if n >= 100:
        return _ONES[n // 100] + ' Hundred' + (' ' + _two_digit_words(n % 100) if n % 100 else '')
    return _two_digit_words(n)


def number_to_words_indian(n):
    """Converts a whole number into words using the Indian numbering system
    (Crore/Lakh/Thousand/Hundred)."""
    n = int(n)
    if n == 0:
        return 'Zero'
    parts = []
    crore, n = divmod(n, 10000000)
    lakh, n = divmod(n, 100000)
    thousand, n = divmod(n, 1000)
    hundred = n
    if crore:
        parts.append(_three_digit_words(crore) + ' Crore')
    if lakh:
        parts.append(_three_digit_words(lakh) + ' Lakh')
    if thousand:
        parts.append(_three_digit_words(thousand) + ' Thousand')
    if hundred:
        parts.append(_three_digit_words(hundred))
    return ' '.join(parts)


def amount_in_words(amount):
    """Renders a rupee amount (Decimal) as 'Rupees ... and ... Paise Only'."""
    amount = Decimal(amount).quantize(Decimal('0.01'))
    rupees = int(amount)
    paise = int((amount - rupees) * 100)
    words = f"Rupees {number_to_words_indian(rupees)}"
    if paise:
        words += f" and {number_to_words_indian(paise)} Paise"
    words += " Only"
    return words


def compute_payslip_for_user(user, payroll_run):
    """Computes and persists a Payslip for `user` for the given PayrollRun.
    Pay is based on actual hours worked (from AttendanceRecord.total_hours),
    not just days present — this handles half-days, early punch-outs, etc.
    PF is prorated to the same earned ratio, so a Non-PF employee (pf_applicable=False)
    simply gets pf_deduction=0 throughout."""
    try:
        structure = user.salary_structure
    except SalaryStructure.DoesNotExist:
        return None

    attendance = AttendanceRecord.objects.filter(
        user=user, date__gte=payroll_run.start_date, date__lte=payroll_run.end_date
    )
    days_present = attendance.exclude(in_time__isnull=True).count()

    total_hours_worked = compute_authorized_hours_for_range(user, payroll_run.start_date, payroll_run.end_date)

    # LOP (Loss of Pay) days are now computed over the exact 26th-25th
    # payroll cycle dates (not a calendar month), so Sundays/PH/CL quota
    # are counted against the real pay window. CL/PH overrides are still
    # sourced from whichever calendar month the cycle's end date falls in.
    lop_days = 0
    try:
        summary = build_payroll_cycle_summary(user, payroll_run.start_date, payroll_run.end_date)
        lop_days = summary['lop']
    except Exception:
        lop_days = 0

    total_days_in_cycle = (payroll_run.end_date - payroll_run.start_date).days + 1
    gross = structure.gross

    if total_days_in_cycle > 0 and gross > 0:
        per_day_gross = gross / Decimal(total_days_in_cycle)
        per_hour_rate = per_day_gross / STANDARD_DAILY_HOURS
        gross_pay = round(per_hour_rate * total_hours_worked, 2)
        # An employee can't be paid more than the full cycle's gross even if
        # their logged hours somehow exceed the standard expectation.
        if gross_pay > gross:
            gross_pay = gross
        earned_ratio = gross_pay / gross
    else:
        gross_pay = Decimal('0.00')
        earned_ratio = Decimal('0.00')

    # Prorate each earnings component individually (so the printed payslip
    # shows the earned Basic/HRA/Conveyance for the cycle, not the full
    # monthly structure values). "Others" (other_allowances) absorbs the
    # rounding remainder so the component rows always sum exactly to gross_pay.
    basic_paid = round(structure.basic * earned_ratio, 2)
    hra_paid = round(structure.hra * earned_ratio, 2)
    conveyance_paid = round(structure.conveyance * earned_ratio, 2)
    allowances_paid = gross_pay - (basic_paid + hra_paid + conveyance_paid)

    pf_deduction = Decimal('0.00')
    if structure.pf_applicable:
        full_cycle_pf = structure.basic * (structure.pf_percent / Decimal('100'))
        pf_deduction = round(full_cycle_pf * earned_ratio, 2)

    bonus = structure.bonus or Decimal('0.00')

    # TDS / P.Tax-LWF / bonus-deduction (recovery) are per-cycle figures HR
    # may enter manually on the payslip (e.g. via the payroll admin screen).
    # Preserve whatever is already there instead of clobbering it on a
    # recompute/regenerate.
    existing = Payslip.objects.filter(payroll_run=payroll_run, user=user).first()
    tds = existing.tds if existing else Decimal('0.00')
    p_tax_lwf = existing.p_tax_lwf if existing else Decimal('0.00')
    bonus_deduction = existing.bonus_deduction if existing else Decimal('0.00')
    other_deductions = existing.other_deductions if existing else Decimal('0.00')

    total_deduction = pf_deduction + tds + p_tax_lwf + bonus_deduction + other_deductions
    net_pay = gross_pay + bonus - total_deduction

    payslip, _ = Payslip.objects.update_or_create(
        payroll_run=payroll_run,
        user=user,
        defaults={
            'basic': basic_paid,
            'hra': hra_paid,
            'conveyance': conveyance_paid,
            'allowances': allowances_paid,
            'bonus': bonus,
            'days_present': days_present,
            'total_hours_worked': total_hours_worked,
            'lop_days': lop_days,
            'gross_pay': gross_pay,
            'pf_deduction': pf_deduction,
            'tds': tds,
            'p_tax_lwf': p_tax_lwf,
            'bonus_deduction': bonus_deduction,
            'other_deductions': other_deductions,
            'net_pay': net_pay,
        }
    )
    return payslip


def generate_payslip_pdf(payslip):
    """Generates a PDF payslip matching the company's official payslip
    layout exactly: blue company name + black 'Payslip' subtitle under a
    thick rule, a bordered employee-info grid, plain black/white
    Earnings/Deduction tables with light-grey headers, and a boxed Net Pay
    footer with the amount in words alongside it."""
    import io
    import os
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from django.core.files.base import ContentFile

    # Register a Unicode font bundled with the app so the ₹ symbol renders
    # correctly regardless of what fonts are installed on the server
    # (Helvetica's built-in encoding doesn't include ₹).
    fonts_dir = os.path.join(os.path.dirname(__file__), 'fonts')
    if 'INR-Sans' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('INR-Sans', os.path.join(fonts_dir, 'DejaVuSans.ttf')))
        pdfmetrics.registerFont(TTFont('INR-Sans-Bold', os.path.join(fonts_dir, 'DejaVuSans-Bold.ttf')))
        pdfmetrics.registerFont(TTFont('INR-Sans-BoldOblique', os.path.join(fonts_dir, 'DejaVuSans-BoldOblique.ttf')))

    user = payslip.user
    profile = getattr(user, 'profile', None)
    company_name = "Swift ProSys (P) Ltd"
    month_label = payslip.payroll_run.start_date.strftime('%B %Y')

    BLUE = colors.HexColor('#2196C9')
    ORANGE = colors.HexColor('#FF6B00')
    GREY_HEADER = colors.HexColor('#EDEDED')
    BORDER = colors.HexColor('#333333')

    # "ProSys" rendered in orange, "Swift" and "(P) Ltd" kept in the
    # company_style's blue — reportlab Paragraphs support this small
    # inline-HTML subset (<font color="...">) for mixed-color text.
    company_name_markup = 'Swift <font color="#FF6B00">ProSys</font> (P) Ltd'

    def rs(value):
        return f"\u20b9 {value:,.2f}"

    total_deduction = (
        payslip.pf_deduction + payslip.tds + payslip.p_tax_lwf
        + payslip.bonus_deduction + payslip.other_deductions
    )
    total_earnings = payslip.basic + payslip.hra + payslip.conveyance + payslip.allowances

    styles = getSampleStyleSheet()
    company_style = ParagraphStyle('Company', parent=styles['Heading1'], alignment=TA_CENTER,
                                    fontSize=22, textColor=BLUE, fontName='INR-Sans-Bold', spaceAfter=0)
    subtitle_style = ParagraphStyle('Subtitle', parent=styles['Heading2'], alignment=TA_CENTER,
                                     fontSize=16, textColor=colors.black, fontName='INR-Sans-Bold', spaceAfter=4)
    words_style = ParagraphStyle('Words', parent=styles['Normal'], fontSize=9.5, leading=13,
                                  fontName='INR-Sans-BoldOblique', alignment=TA_LEFT)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=18 * mm, bottomMargin=20 * mm,
                             leftMargin=18 * mm, rightMargin=18 * mm)
    elements = [
        Paragraph(company_name_markup, company_style),
        Paragraph(f"Payslip - {month_label}", subtitle_style),
    ]

    header_rule = Table([['']], colWidths=[174 * mm], rowHeights=[2.2])
    header_rule.setStyle(TableStyle([('LINEBELOW', (0, 0), (-1, -1), 2.2, colors.black)]))
    elements.append(header_rule)
    elements.append(Spacer(1, 8 * mm))

    # Employee info grid (two columns, label/value pairs each side)
    info_rows = [
        ["Emp. Name", user.get_full_name() or user.username, "UAN No.", (profile.uan_pf_number if profile else '') or '-'],
        ["Emp. No", user.employee_id or '-', "Gross Salary", rs(total_earnings)],
        ["Date of Joining", user.date_joined_company.strftime('%d-%b-%Y') if user.date_joined_company else '-',
         "LOP Days", str(payslip.lop_days)],
    ]
    info_table = Table(info_rows, colWidths=[35 * mm, 52 * mm, 35 * mm, 52 * mm], rowHeights=11 * mm)
    info_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'INR-Sans'),
        ('FONTNAME', (0, 0), (0, -1), 'INR-Sans-Bold'),
        ('FONTNAME', (2, 0), (2, -1), 'INR-Sans-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10.5),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.8, BORDER),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 8 * mm))

    # Earnings / Deduction side-by-side tables
    earnings_rows = [
        ["EARNINGS", ""],
        ["BASIC", rs(payslip.basic)],
        ["HRA", rs(payslip.hra)],
        ["Conveyance", rs(payslip.conveyance)],
        ["Others", rs(payslip.allowances)],
    ]
    if payslip.bonus:
        earnings_rows.append(["Bonus", rs(payslip.bonus)])
    earnings_rows.append(["TOTAL", rs(total_earnings + payslip.bonus)])

    deduction_rows = [
        ["DEDUCTION", ""],
        ["EPF & ESIC", rs(payslip.pf_deduction)],
        ["TDS", rs(payslip.tds)],
        ["P. Tax / LWF", rs(payslip.p_tax_lwf)],
        ["Bonus", rs(payslip.bonus_deduction)],
        ["Total Deduction", rs(total_deduction)],
    ]

    # Pad the shorter table so both render with equal row counts side by side
    while len(deduction_rows) < len(earnings_rows):
        deduction_rows.insert(-1, ["", ""])
    while len(earnings_rows) < len(deduction_rows):
        earnings_rows.insert(-1, ["", ""])

    def styled_table(rows):
        t = Table(rows, colWidths=[42 * mm, 42 * mm], rowHeights=9.5 * mm)
        t.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'INR-Sans'),
            ('FONTNAME', (0, 0), (-1, 0), 'INR-Sans-Bold'),
            ('FONTNAME', (0, -1), (-1, -1), 'INR-Sans-Bold'),
            ('SPAN', (0, 0), (1, 0)),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('BACKGROUND', (0, 0), (-1, 0), GREY_HEADER),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
            ('FONTSIZE', (0, 0), (-1, -1), 10.5),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.8, BORDER),
        ]))
        return t

    side_by_side = Table(
        [[styled_table(earnings_rows), '', styled_table(deduction_rows)]],
        colWidths=[84 * mm, 6 * mm, 84 * mm]
    )
    side_by_side.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    elements.append(side_by_side)
    elements.append(Spacer(1, 10 * mm))

    # Boxed Net Pay footer: label + big blue amount on the left, amount in
    # words (bold italic, wraps) on the right — matching the source layout.
    net_row = [[
        Paragraph("<b>NET PAY:</b>", ParagraphStyle('NetLabel', fontSize=13, fontName='INR-Sans-Bold')),
        Paragraph(rs(payslip.net_pay), ParagraphStyle('NetAmount', fontSize=15, fontName='INR-Sans-Bold',
                                                        textColor=BLUE)),
        Paragraph(amount_in_words(payslip.net_pay), words_style),
    ]]
    net_table = Table(net_row, colWidths=[34 * mm, 40 * mm, 100 * mm], rowHeights=18 * mm)
    net_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1.2, BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (0, 0), 8),
        ('LEFTPADDING', (2, 0), (2, 0), 12),
        ('RIGHTPADDING', (2, 0), (2, 0), 8),
    ]))
    elements.append(net_table)
    elements.append(Spacer(1, 14 * mm))

    footer_rule = Table([['']], colWidths=[174 * mm], rowHeights=[2.2])
    footer_rule.setStyle(TableStyle([('LINEBELOW', (0, 0), (-1, -1), 2.2, colors.black)]))
    elements.append(footer_rule)

    doc.build(elements)
    buffer.seek(0)

    def _filename_slug(payslip):
        import re
        user = payslip.user
        emp_id = user.employee_id or user.username
        full_name = (user.get_full_name() or user.username).strip()
        # Replace spaces with underscores, strip any non-alphanumeric/underscore chars
        safe_name = re.sub(r'[^\w]', '_', full_name)
        safe_name = re.sub(r'_+', '_', safe_name).strip('_')
        return f"{emp_id}_{safe_name}"

    filename = f"{_filename_slug(payslip)}.pdf"
    payslip.pdf_file.save(filename, ContentFile(buffer.read()), save=True)
    return payslip.pdf_file

def generate_allowance_excel(cycle, allowances):
    """Builds an Allowance sheet matching the company format: S.No, Name,
    Project, Attendance Bonus, Travel Allowance, Internet Allowances,
    Incentive <Month Year>, Night Shift Allowances, Grand Total,
    ACCOUNT NO., IFSC, Cleared from Bank, Cleared Date, Ref. No, Remarks."""
    import io
    import calendar
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = Workbook()
    ws = wb.active
    month_label = f"{calendar.month_name[cycle.month]} {cycle.year}"
    ws.title = f"Allowance {cycle.month:02d}-{cycle.year}"[:31]

    header_fill = PatternFill(start_color="0057FF", end_color="0057FF", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)
    thin = Side(style='thin', color='CCCCCC')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    headers = [
        'S. No', 'Name', 'Project', 'Attendance \nBonus', 'Travel Allowance',
        'Internet Allowances', f'Incentive {month_label}', 'Night Shift Allowances',
        'Grand \nTotal', 'ACCOUNT NO.', 'IFSC', 'Cleared from Bank', 'Cleared Date',
        'Ref. No', 'Remarks',
    ]
    for col, h in enumerate(headers, start=1):
        c = ws.cell(row=1, column=col, value=h)
        c.font = header_font
        c.fill = header_fill
        c.alignment = center
        c.border = border

    row = 2
    for i, a in enumerate(allowances, start=1):
        values = [
            i,
            a.user.get_full_name() or a.user.username,
            a.project,
            float(a.attendance_bonus),
            float(a.travel_allowance),
            float(a.internet_allowance),
            float(a.incentive),
            float(a.night_shift_allowance),
            float(a.grand_total),
            a.account_no,
            a.ifsc,
            a.cleared_from_bank,
            a.cleared_date.strftime('%d-%b-%Y') if a.cleared_date else '',
            a.ref_no,
            a.remarks,
        ]
        for col, v in enumerate(values, start=1):
            c = ws.cell(row=row, column=col, value=v)
            c.border = border
            if col not in (2, 3, 10, 11, 12, 13, 14, 15):
                c.alignment = Alignment(horizontal='right')
        row += 1

    widths = [6, 22, 16, 12, 14, 14, 20, 16, 12, 20, 14, 16, 14, 12, 20]
    from openpyxl.utils import get_column_letter
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer
