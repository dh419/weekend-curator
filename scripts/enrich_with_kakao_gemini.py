import html
import json
import os
import re
import time
import requests

KAKAO_LOCAL_KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
KAKAO_LOCAL_ADDRESS_URL = "https://dapi.kakao.com/v2/local/search/address.json"
KAKAO_BLOG_SEARCH_URL = "https://dapi.kakao.com/v2/search/blog"
KAKAO_WEB_SEARCH_URL = "https://dapi.kakao.com/v2/search/web"
KAKAO_DIRECTIONS_URL = "https://apis-navi.kakaomobility.com/v1/directions"

HOME_ADDRESS = "동탄반석로 71"
# Fallback coords for Dongtanbanseok-ro 71 (Hwaseong-si, Gyeonggi-do)
DEFAULT_HOME_COORDS = (127.0706, 37.2063)

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


def search_kakao_place(api_key, query, region_hint=""):
    candidates = []
    q_clean = clean_query(query)
    if region_hint and region_hint not in q_clean:
        candidates.append(f"{region_hint} {q_clean}")
    candidates.append(q_clean)
    # Also try removing trailing parentheses
    no_paren = re.sub(r"\([^)]*\)", "", q_clean).strip()
    if no_paren and no_paren not in candidates:
        candidates.append(no_paren)

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
                    # Prefer Gyeonggi/Seoul/Incheon region if multiple matches
                    for d in docs:
                        addr = d.get("address_name") or d.get("road_address_name") or ""
                        if any(p in addr for p in ("경기", "화성", "용인", "수원", "오산", "안성", "이천", "과천", "성남", "평택", "서울")):
                            return d
                    return docs[0]
        except Exception as e:
            print(f"[Kakao] search_places error ({cand}): {e}")
    return None


def find_kakao_drive(api_key, origin_xy, dest_xy):
    try:
        r = requests.get(
            KAKAO_DIRECTIONS_URL,
            headers=kakao_headers(api_key),
            params={
                "origin": f"{origin_xy[0]},{origin_xy[1]}",
                "destination": f"{dest_xy[0]},{dest_xy[1]}",
                "priority": "RECOMMEND",
            },
            timeout=8,
        )
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
        print(f"[Kakao] directions error: {e}")
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


def collect_kakao_context(data, kakao_key):
    origin_xy = resolve_home_coords(kakao_key)
    print(f"[Kakao] Home coords resolved: {origin_xy}")

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

        food_doc = search_kakao_place(kakao_key, food_q, region_hint) if food_q else None
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

        # Blog searches for place/festival and noodle restaurant
        place_blog_q = f"{title} 아기 유모차 주차 후기" if sp.get("type") != "festival" else f"{title} 후기 주차 일정"
        place_blogs = search_kakao_blogs(kakao_key, place_blog_q, size=4)
        if not place_blogs:
            place_blogs = search_kakao_blogs(kakao_key, title, size=4)

        food_blogs = []
        if food_q:
            food_blogs = search_kakao_blogs(kakao_key, f"{re.sub(r'[^가-힣a-zA-Z0-9 ]', ' ', food_q)} 아기의자 주차 후기", size=3)

        # Web search for festival official schedule/info if festival
        web_info = []
        if sp.get("type") == "festival":
            web_info = search_kakao_web(kakao_key, f"{title} 일정 입장료 주차", size=3)

        # Attach verified clickable blog links directly (max 3: 2 place/festival + 1 food)
        links = []
        for b in place_blogs[:2]:
            links.append({"title": b["title"], "url": b["url"], "source": f"{b['blogname']} · {b['date']}".strip(" ·")})
        for b in food_blogs[:1]:
            links.append({"title": f"[맛집] {b['title']}", "url": b["url"], "source": f"{b['blogname']} · {b['date']}".strip(" ·")})
        sp["blogLinks"] = links

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
            "placeBlogs": [{"title": b["title"], "snippet": b["snippet"], "date": b["date"]} for b in place_blogs],
            "foodBlogs": [{"title": b["title"], "snippet": b["snippet"], "date": b["date"]} for b in food_blogs],
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
        short_kw = re.sub(r"\([^)]*\)|[&·,]", " ", title).strip()
        blogs = search_kakao_blogs(kakao_key, f"{short_kw} 아기 유아식 레시피", size=3)
        if not blogs:
            blogs = search_kakao_blogs(kakao_key, f"{short_kw} 아이 반찬 만들기", size=3)
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
    prompt = f"""
당신은 동탄 가족(첫째 딸 {girl_age}세, 둘째 아들 {boy_age}세)의 주말 큐레이터 데이터 고도화 에디터입니다.
아래 카카오맵/블로그/웹 검색에서 수집한 실제 데이터를 바탕으로, 기존 초안의 모든 항목(나들이/행사·축제 6곳, 집안 놀이 3종, 비매운 아이 요리)을 핵심 위주로 교정·고도화하세요.

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


def enrich_weekend_data(data, girl_age, boy_age, kakao_key=None, gemini_key=None):
    kakao_key = (kakao_key or os.environ.get("KAKAO_REST_API_KEY") or "").strip()
    gemini_key = (gemini_key or os.environ.get("GEMINI_API_KEY") or "").strip()

    # Cap kidsMeals at 6 as requested
    if isinstance(data.get("kidsMeals"), list) and len(data["kidsMeals"]) > 6:
        data["kidsMeals"] = data["kidsMeals"][:6]

    if not kakao_key:
        print("[Enrich] KAKAO_REST_API_KEY not set; skipping Kakao verification.")
        return data

    ctx = collect_kakao_context(data, kakao_key)

    if gemini_key:
        try:
            data = refine_with_gemini(data, ctx, gemini_key, girl_age, boy_age)
        except Exception as e:
            print(f"[Enrich] Gemini refinement skipped due to error: {e}")
    else:
        print("[Enrich] GEMINI_API_KEY not set; keeping Kakao factual corrections & blog links only.")

    return data
