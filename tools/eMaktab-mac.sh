#!/bin/bash
# eMaktab Manager (macOS, Apple chip): o'rnatish va yangilash.
#   bash eMaktab-mac.sh install   -> to'liq o'rnatadi (Desktop'ga), bor bo'lsa joyida yangilaydi
#   bash eMaktab-mac.sh update    -> faqat o'rnatilgan ilovani yangilaydi (akkauntlar va litsenziya joyida)
# Eng yangi reliz GitHub'dan o'zi topiladi; SHA256 tekshiriladi; ma'lumotlardan zaxira olinadi.
# Sinov uchun: EMAKTAB_INSTALL_DIR, EMAKTAB_DATA_DIR, EMAKTAB_RELEASES_URL, EMAKTAB_NO_OPEN.
set -u
MODE="${1:-install}"
REPO="Steadfast11/emaktab-manager-desktop"
APPNAME="eMaktab Manager.app"
ASSET="eMaktab-Manager-macOS-arm64.zip"
INSTALL_DIR="${EMAKTAB_INSTALL_DIR:-$HOME/Desktop}"
DATA="${EMAKTAB_DATA_DIR:-$HOME/Library/Application Support/eMaktab Manager}"
API="${EMAKTAB_RELEASES_URL:-https://api.github.com/repos/$REPO/releases?per_page=30}"

fail() { echo "XATO: $*" >&2; exit 1; }
case "$MODE" in install|update) ;; *) fail "Noma'lum rejim '$MODE'. 'install' yoki 'update' deb yozing." ;; esac
[ "$(uname -s)" = "Darwin" ] || fail "Bu skript faqat macOS uchun."
[ "$(uname -m)" = "arm64" ] || fail "Bu Mac Intel chipli: ilova faqat Apple chipli Mac'lar uchun."

# 1) O'rnatilgan nusxani topamiz
FOUND=""
SEARCH=("$INSTALL_DIR")
[ -z "${EMAKTAB_INSTALL_DIR:-}" ] && SEARCH+=("$HOME/Applications" "/Applications" "$HOME/Downloads" "$HOME/Documents")
for base in "${SEARCH[@]}"; do
  if [ -d "$base/$APPNAME" ]; then FOUND="$base/$APPNAME"; break; fi
done
if [ "$MODE" = "update" ] && [ -z "$FOUND" ]; then
  fail "Ilova o'rnatilmagan. Avval 'install' buyrug'ini ishlating."
fi
TARGET="${FOUND:-$INSTALL_DIR/$APPNAME}"

# 2) Eng yangi relizni topamiz (tag, zip manzili, sha manzili)
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
curl -fsSL "$API" -o "$TMP/releases.json" || fail "GitHub'ga ulanib bo'lmadi. Internetni tekshiring. Hech narsa o'zgarmadi."
INFO="$(/usr/bin/perl -MJSON::PP -e '
  local $/; my $r = decode_json(<STDIN>);
  for my $rel (@$r) { next if $rel->{draft};
    my %a = map { $_->{name} => $_->{browser_download_url} } @{$rel->{assets} || []};
    if ($a{$ARGV[0]} && $a{$ARGV[0] . ".sha256"}) { print "$rel->{tag_name}\n$a{$ARGV[0]}\n$a{$ARGV[0] . q(.sha256)}\n"; exit }
  } exit 1' "$ASSET" < "$TMP/releases.json")" || fail "Reliz topilmadi ($ASSET)."
TAG="$(echo "$INFO" | sed -n 1p)"; ZIP_URL="$(echo "$INFO" | sed -n 2p)"; SHA_URL="$(echo "$INFO" | sed -n 3p)"
MARK="$DATA/app-release.txt"
if [ -n "$FOUND" ] && [ -f "$MARK" ] && [ "$(cat "$MARK" 2>/dev/null)" = "$TAG" ]; then
  echo "Ilova allaqachon yangi ($TAG). Yangilash kerak emas."
  [ -z "${EMAKTAB_NO_OPEN:-}" ] && open "$FOUND"
  exit 0
fi

# 3) Yuklab, tekshiramiz
echo "Yuklanmoqda: $TAG (~70 MB), biroz kuting..."
curl -fL --progress-bar -o "$TMP/app.zip" "$ZIP_URL" || fail "Yuklab bo'lmadi. Hech narsa o'zgarmadi."
EXPECTED="$(curl -fsSL "$SHA_URL" | awk '{print $1}' | head -1)"
GOT="$(shasum -a 256 "$TMP/app.zip" | awk '{print $1}')"
[ -n "$EXPECTED" ] && [ "$GOT" = "$EXPECTED" ] || fail "Yuklangan fayl buzilgan (SHA256 mos emas). Hech narsa o'zgarmadi."
ditto -x -k "$TMP/app.zip" "$TMP/new" || fail "Zipni ochib bo'lmadi. Hech narsa o'zgarmadi."
[ -d "$TMP/new/$APPNAME" ] || fail "Zip ichida '$APPNAME' topilmadi."
if [ -z "${EMAKTAB_SKIP_CODESIGN:-}" ]; then
  codesign --verify --deep --strict "$TMP/new/$APPNAME" 2>/dev/null || fail "Ilova imzosi buzilgan. Hech narsa o'zgarmadi."
fi

# 4) Ochiq ilovani yopamiz (faqat shu yo'ldagi nusxa; akkauntlar yopilganda saqlanadi)
RUNNING="$TARGET/Contents/MacOS"
if [ -d "$TARGET" ] && pgrep -f "$RUNNING" >/dev/null 2>&1; then
  echo "Ilova yopilmoqda..."
  [ -z "${EMAKTAB_INSTALL_DIR:-}" ] && osascript -e 'tell application "eMaktab Manager" to quit' >/dev/null 2>&1
  pgrep -f "$RUNNING" >/dev/null 2>&1 && pkill -TERM -f "$RUNNING" 2>/dev/null
  for _ in $(seq 1 60); do pgrep -f "$RUNNING" >/dev/null 2>&1 || break; sleep 0.5; done
  pgrep -f "$RUNNING" >/dev/null 2>&1 && pkill -KILL -f "$RUNNING" 2>/dev/null && sleep 1
fi

# 5) Ma'lumotlardan zaxira (oxirgi 5 tasi saqlanadi)
if [ -f "$DATA/manager.sqlite3" ]; then
  BK="$DATA/yangilash-zaxira-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$BK" && chmod 700 "$BK"
  for f in manager.sqlite3 manager.sqlite3-wal manager.sqlite3-shm vault.key license.key; do
    [ -f "$DATA/$f" ] && cp -p "$DATA/$f" "$BK/"
  done
  echo "Ma'lumotlar zaxirasi: $BK"
  ls -1d "$DATA"/yangilash-zaxira-* 2>/dev/null | sort -r | tail -n +6 | while read -r old; do rm -rf "$old"; done
fi

# 6) Almashtiramiz (xato bo'lsa eskisi qaytariladi)
mkdir -p "$(dirname "$TARGET")"
if [ -d "$TARGET" ]; then
  mv "$TARGET" "$TARGET.eski" || fail "Eski ilovani siljitib bo'lmadi."
  if ditto "$TMP/new/$APPNAME" "$TARGET"; then rm -rf "$TARGET.eski"
  else rm -rf "$TARGET"; mv "$TARGET.eski" "$TARGET"; fail "O'rnatib bo'lmadi, eski ilova qaytarildi."; fi
else
  ditto "$TMP/new/$APPNAME" "$TARGET" || fail "O'rnatib bo'lmadi."
fi
mkdir -p "$DATA" && echo "$TAG" > "$MARK"
echo "TAYYOR: eMaktab Manager $TAG -> $TARGET. Akkauntlar va litsenziya joyida."
[ -z "${EMAKTAB_NO_OPEN:-}" ] && open "$TARGET"
exit 0
