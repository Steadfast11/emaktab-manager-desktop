# eMaktab Manager desktop

## Foydalanuvchi uchun

Desktop paket birinchi ochilganda qurilma kodini ko‘rsatadi. Dastur egasidan shu
qurilmaga litsenziya olib kiriting; keyingi ochilishlarda qayta so‘ralmaydi.
Kalit boshqa kompyuterda ishlamaydi. Ega uchun: [LICENSING.md](LICENSING.md).

Har bir tizim uchun alohida paket kerak. Windows paketi Mac’da ishlamaydi.
macOS uchun Apple Silicon va Intel ham alohida yig‘iladi.

- macOS: ZIP’ni oching, ichidagi `.app`ni Applications papkasiga ko‘chiring va oching.
- Windows: `.exe`ni foydalanuvchi papkasida saqlang va ikki marta bosing. Administrator huquqi kerak emas.
- Linux: `.bin`ga fayl xususiyatlaridan bajarish huquqini bering va oching. Desktop muhit kerak; terminalsiz oynani ochib bo‘lmaydi. Ubuntu 22.04/24.04 uchun yig‘iladi.

Python o‘rnatish talab qilinmaydi. Brauzer paket ichida bor: birinchi ochishda
foydalanuvchining papkasiga chiqariladi. Keyingi ochilishlarda yana yuklab olinmaydi.
Chrome o‘rnatilgan bo‘lsa boshqaruv oynasi Chrome’da ochiladi; bo‘lmasa paketdagi
Chromium ishlatiladi. Oyna oldinga chiqishi operatsion tizim siyosatiga bog‘liq.
Akkauntlarga kiradigan avtomatlashtirish har doim paketdagi mos Chromium’dan foydalanadi.

Ilovani yana bosish yangi server yaratmaydi: mavjud serverning oynasini ochadi.
8766 port band bo‘lsa boshqa bo‘sh mahalliy port tanlanadi. Brauzer oynasini
yopish serverni to‘xtatmaydi: jadval ishlashda davom etadi. To‘liq yopish uchun
sahifadagi **Dasturni yopish** tugmasini bosing. Kompyuter uxlayotganda yoki
o‘chirilganda ishlar bajarilmaydi. Ushbu paket avtomatik tizimga kirishda ishga tushmaydi.

Linux’ning minimal/headless o‘rnatilishida Chromium tizim kutubxonalari kerak
bo‘lishi mumkin. To‘liq desktop Ubuntu tavsiya etiladi. Ishlab chiquvchi ularni
`python -m playwright install-deps chromium` bilan tayyorlaydi; ilova o‘zi sudo ishlatmaydi.

## Shaxsiy ma’lumotlar

Tarqatma paketlar yaratuvchining akkauntlari, parollari, `data/` papkasi,
brauzer sessiyalari yoki `vault.key`ni o‘z ichiga olmaydi. Yangi kompyuterda
ro‘yxat bo‘sh boshlanadi. Ma’lumotlar dasturning yonida emas, shu joyda saqlanadi:

| Tizim | Papka |
| --- | --- |
| macOS | `~/Library/Application Support/eMaktab Manager` |
| Windows | `%LOCALAPPDATA%\eMaktabManager` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/emaktab-manager` |

Akkauntlar shifrlanadi. Kalit ham shu foydalanuvchi papkasida turadi: kompyuter
egasi yoki administratoridan parollarni mutlaq yashirish va’da qilinmaydi.
Zaxira nusxada bazani va `vault.key`ni birga saqlang, hech kimga yubormang.
Yangilanishda ilovani almashtirish ma’lumotlarni o‘chirmaydi. Dastlabki manbali
saytning `data/` bazasi avtomatik ko‘chirilmaydi; bu shaxsiy migratsiya hisoblanadi.

## Kod va imzo

Backend Nuitka bilan native dasturga kompilyatsiya qilinadi; o‘zimizning `.py`
fayllar tarqatilmaydi. Bu tahlil qilishni qiyinlashtiradi, ammo reverse engineering’ni
imkonsiz qilmaydi. Brauzer oladigan HTML/CSS/JavaScript ko‘rinishi mumkin.
Litsenziya yoki AI uchun yozilgan ko‘rsatma texnik kirishni to‘smaydi.

Mahalliy Mac yig‘ilishi ad-hoc imzolangan. Apple Developer sertifikati va
notarizatsiyasiz internetdan olingan nusxani Gatekeeper tekshirishi mumkin.
Windows paketiga ham haqiqiy code-signing sertifikati bilan imzo qo‘yish kerak;
sertifikatsiz SmartScreen ogohlantirishi mumkin. Himoyalarni o‘chirish bu mahsulotning
o‘rnatish bosqichi emas. Keng tarqatishdan oldin egasining sertifikatlari bilan
imzolash va alohida kompyuterlarda tekshirish zarur.

## Ishlab chiquvchi uchun yig‘ish

Har bir tizimning o‘zida Python 3.12 va C kompilyator kerak: macOS’da Xcode
Command Line Tools, Windows’da MSVC Build Tools, Linux’da GCC va patchelf.
Tayyor paket foydalanuvchisiga ular kerak emas.

```text
python -m pip install -r requirements-release-lock.txt
python -m playwright install chromium
python -m pytest tests -q
python packaging/build.py
```

`packaging/build.py` faqat aniq ruxsat berilgan manba fayllarni alohida staging
papkasiga oladi. Chromium payload’i checksum bilan tekshiriladi, papkadan tashqariga
chiqadigan arxiv yo‘llari rad etiladi. Kompilyatsiya qilingan dastur yangi vaqtinchalik
bazada `--self-test`dan o‘tmasa tarqatma fayl yaratilmaydi. Sinov haqiqiy eMaktab
akkauntiga kirmaydi: UI, parol tahriri, mobil ko‘rinish, shifrlash va bundled browser’ni
mahalliy tekshiradi. Alohida lifecycle test qayta ochish, band port, crash’dan keyin
tiklanish va ma’lumotlar saqlanishini tekshiradi.

`release/` ichidagi yuqori darajadagi ZIP/EXE/BIN va checksum tarqatiladi.
`stage/`, `native/*.build`, compilation report va manbali build kit foydalanuvchiga
yuborilmaydi. Manbani yopiq saqlash kerak bo‘lsa CI uchun private repository ishlating.
`.github/workflows/desktop-build.yml` faqat qo‘lda ishga tushadi: Windows x64,
Linux x64, macOS ARM64 va Intel paketlarini o‘z runner’larida yig‘ib, haqiqiy native
sinovdan o‘tgan artifact’largina beriladi. Bu workflow fayli borligi uning ishlatilgani
yoki platformalar tekshirilganini anglatmaydi.

eMaktab shartlari avtomatlashtirishni cheklashi mumkin. CAPTCHA/2FA foydalanuvchi
tomonidan bajariladi, kirish limitlari aylanib o‘tilmaydi. Kirish muvaffaqiyati rasmiy
faollik hisoblagichiga ta’sir isboti emas.

Texnik asoslar: [Nuitka](https://nuitka.net/user-documentation/user-manual.html),
[Playwright brauzerlari](https://playwright.dev/python/docs/browsers),
[GitHub runner tizimlari](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
