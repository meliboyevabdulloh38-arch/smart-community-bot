from app import reply_text

cases = [
    ("uzbek", "Oybek", "Bugun guruhda nima haqida suhbatlashamiz?"),
    ("russian", "Ойбек", "Что обсудим сегодня?"),
    ("english", "Oybek", "What should we discuss today?"),
]

for language, name, question in cases:
    reply = reply_text(language, name, question)
    assert question not in reply or "nimani anglatishini" not in reply
    print(language, ":", reply)

book_prompt = "Kitoblar haqida gaplashaylik. Oxirgi yillarda o‘qishga arziydigan kitoblar qaysilar?"
book_answer = reply_text("uzbek", "Mubina", book_prompt)
assert "Atom odatlar" in book_answer
assert "Fikringizni tushundim" not in book_answer
print("book:", book_answer)
