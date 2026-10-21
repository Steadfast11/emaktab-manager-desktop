# eMaktab Manager — faqat sayt

FastAPI + Playwright asosidagi mahalliy web-boshqaruv. Android ilova, emulator, ADB, Accessibility va APK funksiyalari faol loyihadan olib tashlangan. Oldingi ishlanmalar faqat `work/retired-android-20261019` arxivida.

## Ishga tushirish

Python 3.11+ bilan:

```sh
./setup.sh
./start.sh
```

Manzil: http://127.0.0.1:8766/ . Boshqa port: `EMAKTAB_PORT=8770 ./start.sh`.
Server faqat loopback’da ishlaydi. Telefonda saytni ochish va serverga masofadan ulanish alohida sozlanadi; brauzer avtomatizatsiyasi server/kompyuterda bajariladi.

## Foydalanish

- Akkauntlarni mahalliy formada qo‘shing; tahrirlashda hozirgi qiymatlar chiqadi. Parol ko‘zcha orqali ochiladi.
- Bo‘lim yarating va nomini o‘zgartiring. Akkauntni boshqa bo‘limga o‘tkazish: Tahrirlash → Bo‘lim.
- Akkaunt yonidagi Run bitta akkauntni, Barchasiga kirish barcha akkauntlarni ketma-ket tekshiradi. Bo‘lim tanlash faqat ro‘yxatni saralaydi.
- Har akkaunt alohida Playwright context’da ishlaydi. Saqlangan sayt sessiyasi haqiqiy kirish dalili bilan tekshiriladi; yaroqli bo‘lsa parol qayta yuborilmaydi.
- CAPTCHA/2FA brauzer oynasida qo‘lda bajariladi; brauzer oynasini ko‘rsatish yoqilgan bo‘lsin. Yashirin rejimda qo‘lda tasdiqlash talab qilinsa sikl to‘xtaydi.
- Noto‘g‘ri parol qayta yuborilmaydi. Noma’lum natija joriy siklni to‘xtatadi; haqiqiy limit esa saqlanadigan kutish holatiga olib keladi. Limit yoki CAPTCHA chetlab o‘tilmaydi.
- Stop vazifani bekor qiladi va boshqariladigan brauzerni yopadi. Brauzer qo‘lda yopilsa ham joriy vazifa bekor qilinadi.
- Jadval ixtiyoriy; server ishlab turishi kerak. Eski rejim sozlamasi avtomatik sayt rejimiga o‘tadi; akkauntlar/bo‘limlar saqlanadi.

## Saqlash

`data/manager.sqlite3` akkauntlar va bo‘limlarni, `data/vault.key` shifrlash kalitini saqlaydi. Parollar va sayt sessiyalari Fernet bilan shifrlanadi. Bu ikkala faylni birga saqlash kerak; kalit yo‘qolsa saqlangan parollarni ochib bo‘lmaydi. Kalit va baza bir qurilmada ekanligi to‘liq qurilma egallanishidan himoya bermaydi.

Server Host/Origin va X-Manager tekshiruvlariga ega. Tahrirlash uchun maxsus mahalliy endpointgina parolni qaytaradi; odatiy holat va loglarda parol yo‘q. API qiymatlari keshlanmaydi. Data katalogini ommaviy serverga joylamang.

## Tekshiruv va chegaralar

```sh
.venv/bin/pip install pytest httpx
.venv/bin/python -m pytest tests -q
```

Testlar haqiqiy login yubormasdan kontekst ajratilishi, saqlangan sessiya qayta ishlatilishi, shifrlash, bo‘limlar, Stop, kutish va faqat web API’ni tekshiradi. `/simulation` alohida sintetik holatlar uchun; haqiqiy akkauntlarni ishlatmaydi. Soxta identifikator haqiqiy IP/ID’ni almashtirmaydi.

Haqiqiy eMaktab kirishi faqat himoyalangan sahifadagi dalil bilan tasdiqlanadi. Sayt tuzilishi o‘zgarsa selektorlar moslashtirilishi mumkin. Kirish rasmiy faol kun hisoblagichi oshganini anglatmaydi. Xizmat shartlari avtomatlashtirishni cheklashi mumkin.
