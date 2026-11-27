import html
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
import requests

KAKAO_LOCAL_KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
KAKAO_LOCAL_ADDRESS_URL = "https://dapi.kakao.com/v2/local/search/address.json"
KAKAO_BLOG_SEARCH_URL = "https://dapi.kakao.com/v2/search/blog"
KAKAO_WEB_SEARCH_URL = "https://dapi.kakao.com/v2/search/web"
KAKAO_DIRECTIONS_URL = "https://apis-navi.kakaomobility.com/v1/directions"
KAKAO_FUTURE_DIRECTIONS_URL = "https://apis-navi.kakaomobility.com/v1/future/directions"
KST = timezone(timedelta(hours=9))
NAVER_BLOG_SEARCH_URL = "https://naverapihub.apigw.ntruss.com/search/v1/blog"

HOME_ADDRESS = os.environ.get("HOME_ADDRESS") or "동탄"


def _load_default_home_coords():
    raw = os.environ.get("HOME_COORDS", "").strip()
    if "," in raw:
        try:
            lon_s, lat_s = raw.split(",", 1)
            return (float(lon_s.strip()), float(lat_s.strip()))
        except ValueError:
            pass
    return (127.0690, 37.2000)


DEFAULT_HOME_COORDS = _load_default_home_coords()

TAG_RE = re.compile(r"<[^>]+>")


def clean_html_text(text):
    if not text:
        return ""
    return html.unescape(TAG_RE.sub("", str(text))).strip()


def clean_query(q):
    if not q:
        return ""
    # Strip parenthetical notes like "(화성 중리)" if overly noisy, keep clean keywords
    return re.sub(r"\s+", " ", str(q)).strip()


def kakao_headers(api_key):
    return {"Authorization": f"KakaoAK {api_key}"}


def resolve_home_coords(api_key):
    try:
        r = requests.get(
            KAKAO_LOCAL_ADDRESS_URL,
            headers=kakao_headers(api_key),
            params={"query": HOME_ADDRESS},
            timeout=6,
        )
        if r.status_code == 200:
            docs = r.json().get("documents") or []
            if docs:
                return float(docs[0]["x"]), float(docs[0]["y"])
    except Exception as e:
        print(f"[Kakao] Home geocode fallback: {e}")
    return DEFAULT_HOME_COORDS


def _build_place_candidates(query, region_hint=""):
    candidates = []

    def add(c):
        c = c.strip()
        if c and c not in candidates:
            candidates.append(c)

    q_clean = clean_query(query)
    no_paren = re.sub(r"\([^)]*\)", "", q_clean).strip()

    # If composite title like "동탄호수공원 & 라크몽", prioritize the primary venue first
    primary = re.split(r"\s*[&/·]\s*", no_paren)[0].strip() if no_paren else ""
    if primary and primary != no_paren:
        add(primary)
        if region_hint and region_hint not in primary:
            add(f"{region_hint} {primary}")

    add(q_clean)
    if no_paren:
        add(no_paren)
    if region_hint and region_hint not in q_clean:
        add(f"{region_hint} {q_clean}")

    # Strip event/exhibition/festival/year suffixes for venue fallback
    stripped = re.sub(
        r"\s+(?:\d+주년.*|20\d{2}.*|크리스마스.*|할로윈.*|겨울.*|가을.*|봄.*|여름.*|(?:특별|기획)?전시?.*|행사.*|축제.*|페스티벌.*|페스타.*)$",
        "",
        primary or no_paren,
    ).strip()
    if stripped:
        add(stripped)
        if region_hint and region_hint not in stripped:
            add(f"{region_hint} {stripped}")

    tokens = (primary or no_paren).split()
    if len(tokens) >= 2:
        add(tokens[0])

    return candidates


NON_SPOT_CATS = {"FD6", "CE7", "AG2", "CS2", "BK9", "PM9", "HP8", "AC5"}
SUB_FACILITY_KEYWORDS = (
    "주차장", "충전소", "매표소", "화장실", "부동산", "공인중개사",
    "요가", "필라테스", "학원", "의원", "한의원", "병원", "약국", "미용실", "네일",
)


def _pick_best_place(docs, query_norm="", is_spot=True):
    region_prefixes = ("경기", "화성", "용인", "수원", "오산", "안성", "이천", "과천", "성남", "평택", "서울")
    scored = []
    for idx, d in enumerate(docs):
        addr = d.get("road_address_name") or d.get("address_name") or ""
        in_region = any(p in addr for p in region_prefixes)
        cat = d.get("category_group_code") or ""
        pname = d.get("place_name") or ""
        pname_norm = re.sub(r"\s+", "", pname).lower()
        penalty = 0
        if not in_region:
            penalty += 100
        if is_spot:
            if cat in NON_SPOT_CATS:
                penalty += 50
            if any(k in pname for k in SUB_FACILITY_KEYWORDS):
                penalty += 40
            if query_norm and pname_norm == query_norm:
                penalty -= 15
        scored.append((penalty, idx, d))
    scored.sort(key=lambda x: (x[0], x[1]))
    return scored[0][2]


def search_kakao_place(api_key, query, region_hint="", is_spot=True):
    candidates = _build_place_candidates(query, region_hint)

    for cand in candidates:
        if not cand:
            continue
        try:
            r = requests.get(
                KAKAO_LOCAL_KEYWORD_URL,
                headers=kakao_headers(api_key),
                params={"query": cand, "size": 5},
                timeout=6,
            )
            if r.status_code == 200:
                docs = r.json().get("documents") or []
                if docs:
                    cand_norm = re.sub(r"\s+", "", cand).lower()
                    picked = _pick_best_place(docs, query_norm=cand_norm, is_spot=is_spot)
                    pname = picked.get("place_name") or ""
                    if is_spot and (
                        (picked.get("category_group_code") or "") in NON_SPOT_CATS
                        or any(k in pname for k in SUB_FACILITY_KEYWORDS)
                    ):
                        # Try next candidate if only non-spot businesses or sub-facilities matched
                        continue
                    return picked
        except Exception as e:
            print(f"[Kakao] search_places error ({cand}): {e}")
    return None


def _next_saturday_noon_kst():
    now_kst = datetime.now(KST)
    days_until_sat = (5 - now_kst.weekday()) % 7
    target = (now_kst + timedelta(days=days_until_sat)).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    if target <= now_kst:
        target += timedelta(days=7)
    return target.strftime("%Y%m%d%H%M")


def find_kakao_drive(api_key, origin_xy, dest_xy):
    dep_time = _next_saturday_noon_kst()
    attempts = [
        (
            KAKAO_FUTURE_DIRECTIONS_URL,
            {
                "origin": f"{origin_xy[0]},{origin_xy[1]}",
                "destination": f"{dest_xy[0]},{dest_xy[1]}",
                "departure_time": dep_time,
                "priority": "RECOMMEND",
            },
        ),
        (
            KAKAO_DIRECTIONS_URL,
            {
                "origin": f"{origin_xy[0]},{origin_xy[1]}",
                "destination": f"{dest_xy[0]},{dest_xy[1]}",
                "priority": "RECOMMEND",
            },
        ),
    ]
    for url, params in attempts:
        try:
            r = requests.get(url, headers=kakao_headers(api_key), params=params, timeout=8)
            if r.status_code == 200:
                routes = r.json().get("routes") or []
                if routes and routes[0].get("result_code") == 0:
                    summary = routes[0].get("summary") or {}
                    dur_sec = summary.get("duration")
                    dist_m = summary.get("distance")
                    if dur_sec is not None and dist_m is not None:
                        mins = max(1, int(round(dur_sec / 60.0)))
                        km = round(dist_m / 1000.0, 1)
                        return mins, km
        except Exception as e:
            print(f"[Kakao] directions error ({url}): {e}")
    return None, None


def search_kakao_blogs(api_key, query, size=4):
    results = []
    seen_urls = set()
    try:
        r = requests.get(
            KAKAO_BLOG_SEARCH_URL,
            headers=kakao_headers(api_key),
            params={"query": clean_query(query), "sort": "accuracy", "size": size},
            timeout=6,
        )
        if r.status_code == 200:
            for d in r.json().get("documents") or []:
                url = d.get("url") or ""
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                dt = (d.get("datetime") or "")[:10]
                results.append({
                    "title": clean_html_text(d.get("title")),
                    "snippet": clean_html_text(d.get("contents")),
                    "url": url,
                    "blogname": clean_html_text(d.get("blogname")),
                    "date": dt,
                })
    except Exception as e:
        print(f"[Kakao] blog search error ({query}): {e}")
    return results


def search_naver_blogs(naver_id, naver_secret, query, display=3):
    if not naver_id or not naver_secret:
        return []
    results = []
    seen_urls = set()
    try:
        r = requests.get(
            NAVER_BLOG_SEARCH_URL,
            headers={
                "X-NCP-APIGW-API-KEY-ID": naver_id,
                "X-NCP-APIGW-API-KEY": naver_secret,
            },
            params={"query": clean_query(query), "display": display, "start": 1, "sort": "sim"},
            timeout=6,
        )
        if r.status_code == 200:
            for item in r.json().get("items") or []:
                url = item.get("link") or ""
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                pdate = item.get("postdate") or ""
                if len(pdate) == 8:
                    pdate = f"{pdate[:4]}-{pdate[4:6]}-{pdate[6:8]}"
                results.append({
                    "title": clean_html_text(item.get("title")),
                    "snippet": clean_html_text(item.get("description")),
                    "url": url,
                    "blogname": clean_html_text(item.get("bloggername")) or "네이버 블로그",
                    "date": pdate,
                })
    except Exception as e:
        print(f"[Naver] blog search error ({query}): {e}")
    return results



def _extract_anchor_tokens(text):
    """Extract core Korean/alphanumeric venue tokens for blog relevance filtering."""
    raw = re.sub(r"\([^)]*\)", " ", text or "")
    raw = re.sub(r"[^가-힣a-zA-Z0-9 ]", " ", raw)
    stop = {
        "경기도", "경기", "수원시", "수원", "용인시", "용인", "화성시", "화성", "성남시", "성남",
        "분당구", "분당", "의왕시", "의왕", "이천시", "이천", "오산시", "오산", "안성시", "안성",
        "과천시", "과천", "평택시", "평택", "처인구", "기흥구", "영통구", "권선구", "동탄",
        "본점", "직영점", "지점", "특별전", "전시", "틈새전시", "축제", "행사", "일루미네이션",
        "주년", "개관", "30주년", "15주년", "2026", "2027", "주말", "나들이"
    }
    tokens = []
    for tok in raw.split():
        if len(tok) < 2 or tok in stop:
            continue
        # Skip administrative neighborhood suffixes (e.g. 서천동, 모가면, 봉담읍)
        if re.match(r"^[가-힣]{2,4}(동|읍|면|리|로|길)$", tok):
            continue
        core = re.sub(r"(본점|직영점|광교점|동탄점|분당.*점|의왕.*점|수원.*점|용인.*점)$", "", tok)
        if len(core) >= 2:
            tokens.append(core)
        elif len(tok) >= 2:
            tokens.append(tok)
    return tokens


def filter_relevant_blogs(blogs, *anchor_texts):
    """Keep only blog results whose title or snippet mentions the target venue/restaurant."""
    tokens = []
    for t in anchor_texts:
        for tok in _extract_anchor_tokens(t):
            if tok not in tokens:
                tokens.append(tok)
    if not tokens:
        return blogs
    # Also include compact forms (spaces removed) for multi-word names like '시몬스 테라스' -> '시몬스테라스'
    compact_anchors = []
    for t in anchor_texts:
        c = re.sub(r"[^가-힣a-zA-Z0-9]", "", t or "")
        if len(c) >= 3:
            compact_anchors.append(c)

    distant_regions = ("김해", "부산", "대구", "울산", "광주", "대전", "세종", "제주", "강릉", "속초", "전주", "여수", "포항", "창원", "경주", "목포", "진영역")
    anchor_joined = " ".join(anchor_texts)
    filtered = []
    for b in blogs:
        hay_raw = f"{b.get('title', '')} {b.get('snippet', '')}"
        if any(dr in hay_raw and dr not in anchor_joined for dr in distant_regions):
            continue
        hay = re.sub(r"\s+", "", hay_raw).lower()
        if any(tok.lower() in hay for tok in tokens) or any(ca.lower() in hay for ca in compact_anchors):
            filtered.append(b)
    return filtered



def _extract_dish_core(title):
    """Strip adjectives and split combined dishes to get a crisp recipe search query."""
    primary = re.split(r"[&·,+/]", title or "")[0]
    primary = re.sub(r"\([^)]*\)", " ", primary)
    adjs = ("맑은", "순한", "부드러운", "달콤한", "고소한", "따뜻한", "든든한", "간단한", "바삭한", "촉촉한", "매운맛 없는", "비매운", "아이")
    for a in adjs:
        primary = re.sub(rf"(?:^|\s){re.escape(a)}(?=\s|$)", " ", primary)
    return re.sub(r"\s+", " ", primary).strip() or (title or "").strip()


def filter_relevant_recipes(blogs, dish_title):
    """Filter out infant/baby-food posts and require the actual dish name in the blog title."""
    baby_block_re = re.compile(
        r"(이유식|무염|완료기|후기이유식|중기이유식|초기이유식|돌아기|돌지난|식단표|식단공유|식판식|식단\s*모음|유아식\s*모음|유아식\s*메뉴|\d+\s*개월)",
        re.IGNORECASE,
    )
    core = _extract_dish_core(dish_title)
    dish_tokens = [t for t in re.sub(r"[^가-힣a-zA-Z0-9 ]", " ", core).split() if len(t) >= 2]
    # The final noun in a Korean dish name is usually the dish type (e.g. 떡만둣국, 감자채전, 계란덮밥, 계란칼국수, 잔치국수, 우동)
    head_noun = dish_tokens[-1] if dish_tokens else ""
    sub_nouns = [head_noun] if head_noun else []
    if len(head_noun) >= 4:
        sub_nouns.append(head_noun[-3:])
        sub_nouns.append(head_noun[-2:])

    scored = []
    for b in blogs:
        t_raw = b.get("title", "")
        hay_raw = f"{t_raw} {b.get('snippet', '')}"
        if baby_block_re.search(hay_raw):
            continue
        t_compact = re.sub(r"\s+", "", t_raw).lower()
        # Require the main dish noun (or at least 2 dish tokens) in the title itself
        hit_head = any(sn.lower() in t_compact for sn in sub_nouns if len(sn) >= 2)
        token_hits = sum(1 for tok in dish_tokens if tok.lower() in t_compact)
        if hit_head or token_hits >= 2:
            # Prefer posts with '레시피' or '만들기' and more token hits
            bonus = (2 if any(k in t_compact for k in ("레시피", "만들기", "끓이기", "굽기")) else 0)
            scored.append((token_hits + bonus, b))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [b for _, b in scored]


def search_kakao_web(api_key, query, size=3):
    results = []
    try:
        r = requests.get(
            KAKAO_WEB_SEARCH_URL,
            headers=kakao_headers(api_key),
            params={"query": clean_query(query), "sort": "accuracy", "size": size},
            timeout=6,
        )
        if r.status_code == 200:
            for d in r.json().get("documents") or []:
                url = d.get("url") or ""
                if not url:
                    continue
                results.append({
                    "title": clean_html_text(d.get("title")),
                    "snippet": clean_html_text(d.get("contents")),
                    "url": url,
                })
    except Exception as e:
        print(f"[Kakao] web search error ({query}): {e}")
    return results


def extract_region_short(addr):
    if not addr:
        return ""
    parts = addr.split()
    # e.g. "경기 화성시 동탄구 ..." -> "화성시" or "경기 용인시 처인구" -> "용인시 처인구"
    filtered = [p for p in parts[:3] if p not in ("경기", "경기도")]
    return " ".join(filtered[:2]) if filtered else parts[0]


def collect_kakao_context(data, kakao_key, naver_id="", naver_secret=""):
    origin_xy = resolve_home_coords(kakao_key)
    print(f"[Kakao] Home coords resolved: {origin_xy}")
    if naver_id and naver_secret:
        print("[Naver] NAVER API HUB blog enrichment enabled.")

    spots_ctx = []
    for idx, sp in enumerate(data.get("spots") or []):
        title = sp.get("title") or ""
        place_q = sp.get("placeQuery") or title
        food_q = sp.get("foodQuery") or sp.get("noodleTag") or ""
        drive_text = sp.get("drive") or ""
        region_hint = ""
        m_reg = re.search(r"\(([^)]+)\)", drive_text)
        if m_reg:
            region_hint = m_reg.group(1)

        place_doc = search_kakao_place(kakao_key, place_q, region_hint)
        if not place_doc and place_q != title:
            place_doc = search_kakao_place(kakao_key, title, region_hint)

        place_xy = None
        if place_doc and place_doc.get("x") and place_doc.get("y"):
            place_xy = (float(place_doc["x"]), float(place_doc["y"]))
            mins, km = find_kakao_drive(kakao_key, origin_xy, place_xy)
            if mins is not None:
                sp["driveMin"] = mins
                addr = place_doc.get("road_address_name") or place_doc.get("address_name") or ""
                reg = extract_region_short(addr) or region_hint
                sp["drive"] = f"🚗 자차 약 {mins}분 ({km}km · {reg})" if reg else f"🚗 자차 약 {mins}분 ({km}km)"
            if place_doc.get("place_url"):
                sp["kakaoPlaceUrl"] = place_doc["place_url"]
            if place_doc.get("road_address_name") or place_doc.get("address_name"):
                sp["placeAddress"] = place_doc.get("road_address_name") or place_doc.get("address_name")
            if place_doc.get("phone"):
                sp["placePhone"] = place_doc["phone"]

        food_doc = search_kakao_place(kakao_key, food_q, region_hint, is_spot=False) if food_q else None
        if food_doc:
            if food_doc.get("place_url"):
                sp["kakaoFoodUrl"] = food_doc["place_url"]
            if food_doc.get("road_address_name") or food_doc.get("address_name"):
                sp["foodAddress"] = food_doc.get("road_address_name") or food_doc.get("address_name")
            if food_doc.get("phone"):
                sp["foodPhone"] = food_doc["phone"]
            if place_xy and food_doc.get("x") and food_doc.get("y"):
                f_xy = (float(food_doc["x"]), float(food_doc["y"]))
                f_min, f_km = find_kakao_drive(kakao_key, place_xy, f_xy)
                if f_min is not None:
                    base_noodle = re.sub(r"\s*\(차로[^)]*\)\s*$", "", sp.get("noodleName") or "").strip()
                    if base_noodle:
                        sp["noodleName"] = f"{base_noodle} (차로 약 {f_min}분)"

        # Blog searches for place/festival and noodle restaurant (Kakao + Naver) with relevance guard
        core_place_q = (place_q or title).strip()
        title_first = (title.split()[0] if title.split() else "")
        if title_first and len(title_first) >= 2 and title_first not in core_place_q and core_place_q in title:
            core_place_q = f"{title_first} {core_place_q}"
        place_blog_q = f"{core_place_q} 아기 유모차 주차 후기" if sp.get("type") != "festival" else f"{core_place_q} 후기 주차 일정"
        place_blogs = filter_relevant_blogs(search_kakao_blogs(kakao_key, place_blog_q, size=5), core_place_q, title)
        if len(place_blogs) < 2:
            place_blogs = filter_relevant_blogs(search_kakao_blogs(kakao_key, f"{core_place_q} 후기", size=5), core_place_q, title)
        naver_place_blogs = filter_relevant_blogs(search_naver_blogs(naver_id, naver_secret, place_blog_q, display=5), core_place_q, title)
        if len(naver_place_blogs) < 2:
            naver_place_blogs = filter_relevant_blogs(search_naver_blogs(naver_id, naver_secret, f"{core_place_q} 후기", display=5), core_place_q, title)

        food_blogs = []
        naver_food_blogs = []
        if food_q:
            food_kw = re.sub(r"[^가-힣a-zA-Z0-9 ]", " ", food_q).strip()
            food_blogs = filter_relevant_blogs(search_kakao_blogs(kakao_key, f"{food_kw} 아기의자 주차 후기", size=4), food_kw)
            if not food_blogs:
                food_blogs = filter_relevant_blogs(search_kakao_blogs(kakao_key, f"{food_kw} 후기", size=4), food_kw)
            naver_food_blogs = filter_relevant_blogs(search_naver_blogs(naver_id, naver_secret, f"{food_kw} 아기의자 주차 후기", display=4), food_kw)
            if not naver_food_blogs:
                naver_food_blogs = filter_relevant_blogs(search_naver_blogs(naver_id, naver_secret, f"{food_kw} 후기", display=4), food_kw)

        # Web search for festival official schedule/info if festival
        web_info = []
        if sp.get("type") == "festival":
            web_info = search_kakao_web(kakao_key, f"{core_place_q} 일정 입장료 주차", size=3)

        # Attach verified clickable blog links directly (reserve up to 3 place + up to 2 food, total max 4)
        place_links = []
        food_links = []
        seen_link_urls = set()
        for b in (naver_place_blogs[:2] + place_blogs[:2]):
            if b["url"] not in seen_link_urls:
                seen_link_urls.add(b["url"])
                place_links.append({"title": b["title"], "url": b["url"], "source": f"{b['blogname']} · {b['date']}".strip(" ·")})
        for b in (naver_food_blogs[:2] + food_blogs[:2]):
            if b["url"] not in seen_link_urls:
                seen_link_urls.add(b["url"])
                food_links.append({"title": f"[맛집] {b['title']}", "url": b["url"], "source": f"{b['blogname']} · {b['date']}".strip(" ·")})
        # Guarantee at least 1 food link when available (up to 3 place + 1 food, or 2+2)
        if food_links:
            sp["blogLinks"] = place_links[:3] + food_links[:1]
        else:
            sp["blogLinks"] = place_links[:4]

        spots_ctx.append({
            "index": idx,
            "type": sp.get("type"),
            "title": title,
            "placeAddress": sp.get("placeAddress", ""),
            "placePhone": sp.get("placePhone", ""),
            "foodAddress": sp.get("foodAddress", ""),
            "foodPhone": sp.get("foodPhone", ""),
            "current": {
                "summary": sp.get("summary", ""),
                "girl": sp.get("girl", ""),
                "boy": sp.get("boy", ""),
                "tip": sp.get("tip", ""),
                "parking": sp.get("parking", ""),
                "noodleDesc": sp.get("noodleDesc", ""),
            },
            "placeBlogs": [{"title": b["title"], "snippet": b["snippet"], "date": b["date"]} for b in (naver_place_blogs + place_blogs)],
            "foodBlogs": [{"title": b["title"], "snippet": b["snippet"], "date": b["date"]} for b in (naver_food_blogs + food_blogs)],
            "webInfo": web_info,
        })
        time.sleep(0.08)

    plays_ctx = []
    for idx, hp in enumerate(data.get("homePlays") or []):
        title = hp.get("title") or ""
        short_kw = re.sub(r"[&·,]", " ", title).strip()
        blogs = search_kakao_blogs(kakao_key, f"{short_kw} 유아 실내놀이 집놀이 후기", size=3)
        if not blogs:
            blogs = search_kakao_blogs(kakao_key, f"{short_kw} 엄마표 놀이", size=3)
        hp["blogLinks"] = [
            {"title": b["title"], "url": b["url"], "source": f"{b['blogname']} · {b['date']}".strip(" ·")}
            for b in blogs[:2]
        ]
        plays_ctx.append({
            "index": idx,
            "title": title,
            "current": {"prep": hp.get("prep", ""), "girl": hp.get("girl", ""), "boy": hp.get("boy", ""), "tip": hp.get("tip", "")},
            "blogs": [{"title": b["title"], "snippet": b["snippet"]} for b in blogs],
        })
        time.sleep(0.06)

    meals_ctx = []
    for idx, km_item in enumerate(data.get("kidsMeals") or []):
        title = km_item.get("title") or ""
        core_dish = _extract_dish_core(title)
        n_blogs = search_naver_blogs(naver_id, naver_secret, f"{core_dish} 황금레시피 만들기", display=6)
        k_blogs = search_kakao_blogs(kakao_key, f"{core_dish} 레시피 만들기", size=5)
        blogs = filter_relevant_recipes(n_blogs + k_blogs, title)
        if len(blogs) < 2:
            n_fb = search_naver_blogs(naver_id, naver_secret, f"{core_dish} 레시피", display=6)
            k_fb = search_kakao_blogs(kakao_key, f"{core_dish} 만들기", size=5)
            blogs = filter_relevant_recipes(n_blogs + k_blogs + n_fb + k_fb, title)
        seen_m = set()
        dedup_blogs = []
        for b in blogs:
            if b["url"] not in seen_m:
                seen_m.add(b["url"])
                dedup_blogs.append(b)
        blogs = dedup_blogs
        km_item["blogLinks"] = [
            {"title": b["title"], "url": b["url"], "source": f"{b['blogname']} · {b['date']}".strip(" ·")}
            for b in blogs[:2]
        ]
        meals_ctx.append({
            "index": idx,
            "title": title,
            "current": {"meta": km_item.get("meta", ""), "recipe": km_item.get("recipe", ""), "tip": km_item.get("tip", "")},
            "blogs": [{"title": b["title"], "snippet": b["snippet"]} for b in blogs],
        })
        time.sleep(0.06)

    return {"spots": spots_ctx, "homePlays": plays_ctx, "kidsMeals": meals_ctx}


def refine_with_gemini(data, ctx, gemini_key, girl_age, boy_age):
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=gemini_key)
    family_profile = os.environ.get("FAMILY_PROFILE") or f"은우 {girl_age}세, 우준 {boy_age}세"
    prompt = f"""
당신은 동탄 가족({family_profile})의 주말 큐레이터 데이터 고도화 에디터입니다.
아래 카카오맵/블로그/웹 검색에서 수집한 실제 데이터를 바탕으로, 기존 초안의 모든 항목(나들이/행사·축제 6곳, 집안 놀이 3종, 비매운 아이 요리)을 핵심 위주로 교정·고도화하세요.
(참고: 출력 텍스트에는 상세 거주지 도로명 주소나 출생연도를 직접 적지 말고 '동탄', '은우/우준'으로만 표기하세요.)

[작성 원칙]
1. 광고·인사말은 모두 걸러내고 실제 부모 후기에서 나온 팩트(유모차 동선, 기저귀교환대, 주차 요금/혼잡, 행사 기간·예약, 아기의자, 층간소음/준비물 팁, 맵지 않게 간 맞추는 레시피 팁)만 반영하세요.
2. `spots` 각 항목:
   - `blogSummary`: 장소/행사 블로그 후기 핵심 요약 2~3문장 (실제 방문 팁 중심, 개조식 불릿이나 간결한 문장)
   - `noodleBlogSummary`: 국수집 블로그 후기 핵심 요약 1~2문장 (아기의자·주차·순한 메뉴 중심)
   - `summary`, `girl`, `boy`, `tip`, `parking`, `noodleDesc`: 기존 초안에 수집된 팩트를 녹여 더 구체적이고 실용적으로 보강하세요. (주차장에 실제 주소나 팁이 확인되면 자연스럽게 포함)
3. `homePlays` 각 항목:
   - `blogSummary`: 블로그 실전 놀이 후기/주의점 핵심 요약 1~2문장
   - `tip`: 층간소음/안전/정리 팁 고도화
4. `kidsMeals` 각 항목:
   - `blogSummary`: 블로그 레시피 핵심 노하우(간 조절·아이 잘 먹는 팁) 요약 1~2문장
   - `recipe`, `tip`: 더 따라 하기 쉽게 고도화 (맵기 0% 엄수)
5. 절대 없는 사실이나 URL을 지어내지 마세요. 마크다운 코드블록 없이 순수 JSON 객체만 출력하세요.

[수집 데이터]
{json.dumps(ctx, ensure_ascii=False)}

[출력 JSON 스키마]
{{
  "spots": [
    {{
      "index": 0,
      "summary": "...",
      "girl": "...",
      "boy": "...",
      "tip": "...",
      "parking": "...",
      "noodleDesc": "...",
      "blogSummary": "...",
      "noodleBlogSummary": "..."
    }}
  ],
  "homePlays": [
    {{
      "index": 0,
      "tip": "...",
      "blogSummary": "..."
    }}
  ],
  "kidsMeals": [
    {{
      "index": 0,
      "recipe": "...",
      "tip": "...",
      "blogSummary": "..."
    }}
  ]
}}
"""
    candidate_models = [
        os.environ.get("GEMINI_MODEL", "").strip(),
        "gemini-3.8-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.7-flash",
        "gemini-3.5-flash",
    ]
    seen = set()
    models_to_try = [m for m in candidate_models if m and not (m in seen or seen.add(m))]

    refined = None
    for m in models_to_try:
        try:
            print(f"[Gemini] Refining weekend content with '{m}'...")
            res = client.models.generate_content(
                model=m,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    response_mime_type="application/json",
                ),
            )
            txt = (res.text or "").strip()
            txt = re.sub(r"^```(?:json)?\s*", "", txt)
            txt = re.sub(r"\s*```$", "", txt)
            refined = json.loads(txt)
            print(f"[Gemini] Refinement succeeded with '{m}'.")
            break
        except Exception as e:
            print(f"[Gemini] Model '{m}' failed: {e}")

    if not isinstance(refined, dict):
        return data

    # Merge refined text back while keeping pinned coordinates, driveMin, URLs intact
    spots = data.get("spots") or []
    for item in refined.get("spots") or []:
        idx = item.get("index")
        if isinstance(idx, int) and 0 <= idx < len(spots):
            for field in ("summary", "girl", "boy", "tip", "parking", "noodleDesc", "blogSummary", "noodleBlogSummary"):
                val = (item.get(field) or "").strip()
                if val:
                    spots[idx][field] = val

    plays = data.get("homePlays") or []
    for item in refined.get("homePlays") or []:
        idx = item.get("index")
        if isinstance(idx, int) and 0 <= idx < len(plays):
            for field in ("tip", "blogSummary"):
                val = (item.get(field) or "").strip()
                if val:
                    plays[idx][field] = val

    meals = data.get("kidsMeals") or []
    for item in refined.get("kidsMeals") or []:
        idx = item.get("index")
        if isinstance(idx, int) and 0 <= idx < len(meals):
            for field in ("recipe", "tip", "blogSummary"):
                val = (item.get(field) or "").strip()
                if val:
                    meals[idx][field] = val

    return data



def normalize_extra_fields(data):
    """Normalize alternate Gemini JSON keys (girlPoint/boyPoint/materials/cookTime) to template keys."""
    for hp in data.get("homePlays") or []:
        if not hp.get("prep") and hp.get("materials"):
            hp["prep"] = f"🧰 준비물: {hp['materials']}"
        if not hp.get("girl") and hp.get("girlPoint"):
            hp["girl"] = hp["girlPoint"]
        if not hp.get("boy") and hp.get("boyPoint"):
            hp["boy"] = hp["boyPoint"]
        if not hp.get("tip") and hp.get("parentTip"):
            hp["tip"] = hp["parentTip"]
    for km in data.get("kidsMeals") or []:
        if not km.get("meta"):
            parts = []
            if km.get("cookTime"):
                parts.append(f"⏱️ {km['cookTime']}")
            if km.get("ingredients"):
                parts.append(str(km["ingredients"]))
            if parts:
                km["meta"] = " · ".join(parts)
        if not km.get("recipe") and km.get("steps"):
            km["recipe"] = km["steps"]
        if not km.get("tip") and km.get("kidTip"):
            km["tip"] = km["kidTip"]
    return data


def enrich_weekend_data(data, girl_age, boy_age, kakao_key=None, gemini_key=None, naver_id=None, naver_secret=None):
    kakao_key = (kakao_key or os.environ.get("KAKAO_REST_API_KEY") or "").strip()
    gemini_key = (gemini_key or os.environ.get("GEMINI_API_KEY") or "").strip()
    naver_id = (naver_id or os.environ.get("NAVER_CLIENT_ID") or "").strip()
    naver_secret = (naver_secret or os.environ.get("NAVER_CLIENT_SECRET") or "").strip()

    normalize_extra_fields(data)

    # Cap kidsMeals at 6 as requested
    if isinstance(data.get("kidsMeals"), list) and len(data["kidsMeals"]) > 6:
        data["kidsMeals"] = data["kidsMeals"][:6]

    if not kakao_key:
        print("[Enrich] KAKAO_REST_API_KEY not set; skipping Kakao verification.")
        return data

    ctx = collect_kakao_context(data, kakao_key, naver_id=naver_id, naver_secret=naver_secret)

    if gemini_key:
        try:
            data = refine_with_gemini(data, ctx, gemini_key, girl_age, boy_age)
        except Exception as e:
            print(f"[Enrich] Gemini refinement skipped due to error: {e}")
    else:
        print("[Enrich] GEMINI_API_KEY not set; keeping Kakao factual corrections & blog links only.")

    return data
