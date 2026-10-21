# Qurilmaga bog‘langan litsenziya

Desktop paket ochilganda litsenziya bo‘lmasa aktivatsiya oynasi chiqadi. U yerda
qurilma kodini nusxalash mumkin. Foydalanuvchi kodni egasiga yuboradi; egasi shu
kodga imzolangan litsenziya beradi. To‘g‘ri kalit kiritilgandan keyin boshqaruv
ochiladi. Keyingi ochilishlarda kalit qayta so‘ralmaydi.

Ed25519 raqamli imzosi ishlatiladi: tarqatmada faqat `license_config.py`dagi ochiq
tekshirish kaliti kompilyatsiya qilinadi. Kalit yaratish algoritmini bilish yoki
ochiq kalitni ko‘rish yangi haqiqiy litsenziya yaratish imkonini bermaydi.
Mijoz paketi litsenziya yaratadigan tool yoki maxfiy signing key’ni olmaydi.

Qurilma kodi macOS’da IOPlatformUUID, Windows’da MachineGuid, Linux’da machine-id
asosida, mahsulotga xos SHA-256 hash sifatida olinadi. MAC manzil yoki IP’ga
bog‘lanmaydi. Boshqa qurilmaga ko‘chirilgan litsenziya va ma’lumotlar papkasi
aktivatsiyadan o‘tmaydi. OS qayta o‘rnatilganda yoki VM klonlanganda identifikator
o‘zgarishi yoki nusxalanishi mumkin. Bu oddiy tarqatishga qarshi himoya;
administratorning identifikatorni soxtalashtirishiga yoki native binary’ni
o‘zgartirishiga qarshi mutlaq kafolat emas.

Bu — muddatsiz, offline litsenziya. Masofaviy bekor qilish, faollashtirishlar
hisobi va kalitlarni almashtirish serveri hozir yo‘q. Bu imkoniyatlar kerak bo‘lsa
alohida markaziy litsenziya xizmati zarur. Oddiy foydalanuvchiga dasturni ochish
uchun internet talab qilinmaydi; eMaktabning o‘ziga kirish uchun kerak bo‘ladi.

## Faqat egasi uchun

Shu Mac’da `License Creator.command`ni oching. Mijozning qurilma kodini kiriting,
kalit yarating va mijozga faqat chiqqan litsenziyani yuboring. Bu yordamchi
manbali loyiha va uning `.venv` muhiti bilan ishlaydi; uni mijozga yubormang.

Maxfiy imzo kaliti loyiha, tarqatma va GitHub’dan tashqarida:
`~/Library/Application Support/eMaktab Manager Authority/issuer-private.pem`.
Ushbu papkani maxfiy, xavfsiz joyga zaxiralang. Yo‘qolsa mavjud litsenziyalar
ishlashda davom etadi, lekin yangi mos kalit chiqarish imkoni yo‘qoladi.
Kalit sizib chiqsa, begonalar ham litsenziya chiqara oladi; bu holda ishonch
kalitini almashtirish va yangi tarqatma kerak. Uni chatga yoki repository’ga yuklamang.

CLI ham bor: `tools/license_issuer.py --help`. `--init-public` faqat yangi
authority yaratish yoki uning ochiq tekshirish kalitini chiqarish uchun.
Bir mahsulot versiyalari bir xil authority’ni saqlashi kerak. GitHub Actions
faqat ochiq kalit bilan yig‘adi, maxfiy kalit CI’ga ham yuborilmaydi.

`--self-test` faqat vaqtinchalik bazada UI va shifrlashni tekshiradi. U orqali
haqiqiy akkauntlarni ishga tushirish, jadval yoqish va limitni tiklash taqiqlangan.
Litsenziyasiz oddiy ishga tushirishda akkaunt API’lari va avtomatik jadval yopiq.
Oddiy `start.sh` esa dasturchining manbali muhiti, mijozga tarqatiladigan mahsulot emas.
