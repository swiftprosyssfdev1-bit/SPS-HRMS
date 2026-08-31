import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from chatbot.matcher import get_reply
from chatbot.models import ChatLog


def _escalate_to_hr(user, message):
    """When the bot has no answer, raise a real HR query instead of just
    telling the employee to go do it themselves. Returns the reply text,
    or None if no HR user could be found (falls back to the old message).
    Never routes to the requester themselves — if an HR/Admin user asks,
    this looks for a DIFFERENT HR/Admin colleague instead of "forwarding"
    the question back to the same person who asked it."""
    from core.models import User
    from queries.models import EmployeeQuery

    hr_user = (
        User.objects.filter(role=User.ROLE_HR).exclude(id=user.id).first()
        or User.objects.filter(role=User.ROLE_SENIOR_HR).exclude(id=user.id).first()
        or User.objects.filter(role=User.ROLE_ADMIN).exclude(id=user.id).first()
    )
    if not hr_user:
        return None

    EmployeeQuery.objects.create(
        user=user,
        recipient_role=EmployeeQuery.RECIPIENT_HR,
        recipient_user=hr_user,
        message=message,
    )
    return (
        "I don't have an automatic answer for that, so I've forwarded your "
        "question to HR as a query. You can track it under My Queries and "
        "they'll respond there."
    )


@login_required
@require_POST
def chatbot_ask(request):
    try:
        data = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        data = request.POST

    message = (data.get('message') or '').strip()
    if not message:
        return JsonResponse({"reply": "Please type a question."}, status=400)

    # Short-term memory for follow-up questions ("what about casual leave?").
    # Kept in the session (not the DB) — resets per browser session, which is
    # fine for this purpose and needs no schema change / migration.
    history = request.session.get('chatbot_history', [])

    result = get_reply(request.user, message, chat_history=history)

    ChatLog.objects.create(
        user=request.user,
        question=message[:500],
        answer_source=result["source"],
        matched_faq=result["faq"],
    )

    # Only remember turns that produced a real answer — an UNKNOWN reply is
    # a dead end, not context worth carrying into the next question.
    if result["source"] != "UNKNOWN":
        history.append({"q": message[:500], "a": result["reply"][:500]})
        request.session['chatbot_history'] = history[-2:]

    if result["source"] == "UNKNOWN":
        # Don't auto-escalate — let the employee choose what happens next.
        # An HR/Admin user is already HR, so "Forward to HR" is relabeled
        # to make clear it goes to a different HR/Admin colleague.
        forward_label = "Forward to another HR" if request.user.is_hr_or_admin() else "Forward to HR"
        return JsonResponse({
            "reply": result["reply"],
            "options": [
                {"action": "forward_hr", "label": forward_label},
                {"action": "denied", "label": "Denied"},
            ],
            "message": message,
        })

    return JsonResponse({"reply": result["reply"]})


@login_required
@require_POST
def chatbot_resolve(request):
    """Handles the employee's choice after an unanswered question:
    forward it to HR as a real query, or drop it."""
    try:
        data = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        data = request.POST

    action = (data.get('action') or '').strip()
    message = (data.get('message') or '').strip()[:500]

    if action == 'forward_hr':
        escalated_reply = _escalate_to_hr(request.user, message) if message else None
        reply = escalated_reply or (
            "I couldn't find an HR user to forward this to. Please raise a query manually."
        )
        return JsonResponse({"reply": reply})

    if action == 'denied':
        return JsonResponse({
            "reply": "Okay, I won't forward that. Let me know if there's anything else I can help with."
        })

    return JsonResponse({"reply": "Sorry, I didn't understand that choice."}, status=400)
