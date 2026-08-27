with open("employees/views.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

new_lines = []
for i, line in enumerate(lines):
    if "for emp in onboarding_users:" in line and i + 1 < len(lines) and "except ValueError:" in lines[i+1]:
        new_lines.append(line)
        new_lines.extend([
            "        team_rows.append({\n",
            "            'emp': emp,\n",
            "            'tf': training_records.get(emp.id),\n",
            "        })\n",
            "\n",
            "    return render(request, 'employees/my_team_training.html', {\n",
            "        'team_rows': team_rows,\n",
            "    })\n",
            "\n",
            "@login_required\n",
            "def download_document(request, doc_id):\n",
            "    import os\n",
            "    from django.http import FileResponse, Http404\n",
            "    from employees.models import EmployeeDocument\n",
            "    \n",
            "    doc = get_object_or_404(EmployeeDocument, id=doc_id)\n",
            "    \n",
            "    if doc.user_id != request.user.id and not request.user.is_hr_or_admin():\n",
            "        messages.error(request, 'You do not have permission to download this document.')\n",
            "        return redirect('core:dashboard')\n",
            "        \n",
            "    if not doc.file or not os.path.exists(doc.file.path):\n",
            "        raise Http404('File not found')\n",
            "        \n",
            "    response = FileResponse(open(doc.file.path, 'rb'))\n",
            "    response['Content-Disposition'] = f'attachment; filename=\"{os.path.basename(doc.file.name)}\"'\n",
            "    return response\n"
        ])
        break
    else:
        new_lines.append(line)

with open("employees/views.py", "w", encoding="utf-8") as f:
    f.writelines(new_lines)
