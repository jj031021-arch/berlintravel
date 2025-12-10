import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import requests
import google.generativeai as genai
import googlemaps
import plotly.express as px
import yfinance as yf  # 환율 그래프용
from datetime import datetime, timedelta

# ---------------------------------------------------------
# 🚨 파일 이름 설정 (엑셀 파일명)
# ---------------------------------------------------------
CRIME_FILE_NAME = "2023_berlin_crime.xlsx"

# ---------------------------------------------------------
# 1. 설정 및 API 키
# ---------------------------------------------------------
st.set_page_config(layout="wide", page_title="베를린 통합 가이드")

GMAPS_API_KEY = st.secrets.get("google_maps_api_key", "")
GEMINI_API_KEY = st.secrets.get("gemini_api_key", "")

if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
    except:
        pass

# ---------------------------------------------------------
# 2. 데이터 처리 함수 (번역 및 차트 기능 추가)
# ---------------------------------------------------------

# [환율] 현재가 + 히스토리 데이터
@st.cache_data
def get_exchange_data():
    try:
        # 야후 파이낸스에서 EUR/KRW 데이터 가져오기
        ticker = yf.Ticker("EURKRW=X")
        # 현재가
        current_data = ticker.history(period="1d")
        current_rate = current_data['Close'].iloc[-1]
        
        # 1개월치 데이터 (차트용)
        hist_data = ticker.history(period="1mo")
        return current_rate, hist_data
    except:
        return 1450.0, pd.DataFrame()

# [날씨] 현재 + 7일 예보
@st.cache_data
def get_weather_forecast():
    try:
        # 베를린 위경도, daily 예보 포함
        url = "https://api.open-meteo.com/v1/forecast?latitude=52.52&longitude=13.41&current_weather=true&daily=temperature_2m_max,temperature_2m_min&timezone=auto"
        data = requests.get(url).json()
        
        current = data['current_weather']
        daily = data['daily']
        
        # 예보 데이터프레임 생성
        forecast_df = pd.DataFrame({
            'Date': daily['time'],
            'Max Temp': daily['temperature_2m_max'],
            'Min Temp': daily['temperature_2m_min']
        })
        return current, forecast_df
    except:
        return {"temperature": 15.0, "weathercode": 0}, pd.DataFrame()

@st.cache_data
def load_crime_data_excel(file_name):
    try:
        df = pd.read_excel(file_name, skiprows=4, engine='openpyxl')
        df.columns = [str(c).replace('\n', ' ').strip() for c in df.columns]
        
        district_col = None
        total_col = None
        for c in df.columns:
            if 'Bezeichnung' in c: district_col = c
            if 'Straftaten' in c and 'insgesamt' in c: total_col = c
        
        if not district_col: return pd.DataFrame()

        berlin_districts = [
            "Mitte", "Friedrichshain-Kreuzberg", "Pankow", "Charlottenburg-Wilmersdorf", 
            "Spandau", "Steglitz-Zehlendorf", "Tempelhof-Schöneberg", "Neukölln", 
            "Treptow-Köpenick", "Marzahn-Hellersdorf", "Lichtenberg", "Reinickendorf"
        ]
        df = df[df[district_col].isin(berlin_districts)].copy()

        if total_col:
            df[total_col] = df[total_col].astype(str).str.replace('.', '', regex=False)
            df['Total_Crime'] = pd.to_numeric(df[total_col], errors='coerce').fillna(0)
        
        cols_to_clean = [c for c in df.columns if c != district_col and 'LOR' not in c]
        for c in cols_to_clean:
            df[c] = df[c].astype(str).str.replace('.', '', regex=False)
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)

        df = df.rename(columns={district_col: 'District'})
        return df
    except:
        return pd.DataFrame()

# [범죄 유형 한글 번역 맵핑]
def translate_crime_columns(df):
    translation_map = {
        'Raub': '강도',
        'Straßenraub, Handtaschen-raub': '노상/소매치기',
        'Körper-verletzungen -insgesamt-': '상해 (전체)',
        'Gefährl. und schwere Körper-verletzung': '중상해',
        'Freiheits-beraubung, Nötigung, Bedrohung, Nachstellung': '협박/스토킹',
        'Diebstahl -insgesamt-': '절도 (전체)',
        'Diebstahl von Kraftwagen': '차량 절도',
        'Diebstahl an/aus Kfz': '차량털이',
        'Fahrrad-diebstahl': '자전거 절도',
        'Wohnraum-einbruch': '주거 침입',
        'Branddelikte -insgesamt-': '화재 범죄',
        'Brand-stiftung': '방화',
        'Sach-beschädigung -insgesamt-': '기물 파손',
        'Sach-beschädigung durch Graffiti': '그래피티',
        'Rauschgift-delikte': '마약 범죄',
        'Kieztaten': '기타 지역 범죄',
        'Straftaten -insgesamt-': '총 범죄'
    }
    
    # 컬럼명 변경이 아니라, 데이터를 시각화할 때 라벨을 바꾸기 위해 딕셔너리 반환
    return translation_map

@st.cache_data
def get_osm_places(category, lat, lng, radius_m=3000, cuisine_filter=None):
    overpass_url = "http://overpass-api.de/api/interpreter"
    if category == 'restaurant': tag = '["amenity"="restaurant"]'
    elif category == 'hotel': tag = '["tourism"="hotel"]'
    elif category == 'tourism': tag = '["tourism"~"attraction|museum|artwork|viewpoint"]'
    else: return []

    query = f"""[out:json];(node{tag}(around:{radius_m},{lat},{lng}););out body;"""
    try:
        response = requests.get(overpass_url, params={'data': query})
        data = response.json()
        results = []
        
        cuisine_map = {
            "한식": ["korean"], "양식": ["italian","french","german","american","burger","pizza","steak"],
            "일식": ["japanese","sushi","ramen"], "중식": ["chinese","dim sum"],
            "아시안": ["vietnamese","thai","asian","indian"], "카페": ["coffee","cafe","cake","bakery"]
        }

        for element in data['elements']:
            if 'tags' in element and 'name' in element['tags']:
                name = element['tags']['name']
                raw_cuisine = element['tags'].get('cuisine', 'general').lower()
                
                detected_type = "기타"
                if category == 'restaurant':
                    is_match = False
                    if cuisine_filter and "전체" not in cuisine_filter:
                        for user_select in cuisine_filter:
                            if user_select in cuisine_map:
                                if any(c in raw_cuisine for c in cuisine_map[user_select]):
                                    is_match = True; detected_type = user_select; break
                            elif user_select == "기타": is_match = True 
                        if not is_match: continue
                    else:
                        for k, v in cuisine_map.items():
                            if any(c in raw_cuisine for c in v): detected_type = k; break

                search_query = f"{name} Berlin".replace(" ", "+")
                link = f"https://www.google.com/search?q={search_query}"
                
                desc = "장소"
                if category == 'restaurant': desc = f"음식점 ({detected_type})"
                elif category == 'hotel': desc = "숙박시설"
                elif category == 'tourism': desc = "관광명소"

                results.append({"name": name, "lat": element['lat'], "lng": element['lon'], "type": category, "desc": desc, "link": link})
        return results
    except: return []

def search_location(query):
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {'q': query, 'format': 'json', 'limit': 1}
        headers = {'User-Agent': 'BerlinApp/1.0'}
        res = requests.get(url, params=params, headers=headers).json()
        if res: return float(res[0]['lat']), float(res[0]['lon']), res[0]['display_name']
    except: pass
    return None, None, None

def get_gemini_response(prompt):
    if not GEMINI_API_KEY: return "API 키 확인 필요"
    try:
        model = genai.GenerativeModel('gemini-pro')
        response = model.generate_content(prompt)
        return response.text
    except: return "AI 서비스 오류"

# ---------------------------------------------------------
# 3. 여행 코스 데이터
# ---------------------------------------------------------
courses = {
    "🌳 Theme 1: 숲과 힐링": [
        {"name": "1. 전승기념탑", "lat": 52.5145, "lng": 13.3501, "desc": "베를린 전경이 한눈에 보이는 황금 천사상"},
        {"name": "2. 티어가르텐 산책", "lat": 52.5135, "lng": 13.3575, "desc": "도심 속 거대한 허파"},
        {"name": "3. Cafe am Neuen See (점심)", "lat": 52.5076, "lng": 13.3448, "desc": "호수 앞 비어가든 (피자/맥주)"},
        {"name": "4. 베를린 동물원", "lat": 52.5079, "lng": 13.3377, "desc": "세계 최대 종을 보유한 동물원"},
        {"name": "5. 카이저 빌헬름 교회", "lat": 52.5048, "lng": 13.3350, "desc": "전쟁의 상처를 간직한 교회"}
    ],
    "🎨 Theme 2: 예술과 고전": [
        {"name": "1. 베를린 돔", "lat": 52.5190, "lng": 13.4010, "desc": "웅장한 돔 지붕"},
        {"name": "2. 구 국립 미술관", "lat": 52.5208, "lng": 13.3982, "desc": "고전 예술의 정수"},
        {"name": "3. Monsieur Vuong (맛집)", "lat": 52.5244, "lng": 13.4085, "desc": "유명 베트남 쌀국수 맛집"},
        {"name": "4. Hackescher Hof", "lat": 52.5246, "lng": 13.4020, "desc": "아르누보 양식의 안뜰"},
        {"name": "5. 제임스 사이먼 공원", "lat": 52.5213, "lng": 13.4005, "desc": "강변 산책로"}
    ],
    "🏰 Theme 3: 분단의 역사": [
        {"name": "1. 베를린 장벽 기념관", "lat": 52.5352, "lng": 13.3903, "desc": "장벽의 실제 모습"},
        {"name": "2. Mauerpark", "lat": 52.5404, "lng": 13.4048, "desc": "주말 벼룩시장과 공원"},
        {"name": "3. Prater Beer Garden", "lat": 52.5399, "lng": 13.4101, "desc": "가장 오래된 야외 맥주집"},
        {"name": "4. 체크포인트 찰리", "lat": 52.5074, "lng": 13.3904, "desc": "분단 시절 검문소"},
        {"name": "5. Topography of Terror", "lat": 52.5065, "lng": 13.3835, "desc": "나치 역사관"}
    ],
    "🕶️ Theme 4: 힙스터 성지": [
        {"name": "1. 이스트 사이드 갤러리", "lat": 52.5050, "lng": 13.4397, "desc": "장벽 위 야외 갤러리"},
        {"name": "2. 오버바움 다리", "lat": 52.5015, "lng": 13.4455, "desc": "붉은 벽돌 다리"},
        {"name": "3. Burgermeister (맛집)", "lat": 52.5005, "lng": 13.4420, "desc": "다리 밑 힙한 버거집"},
        {"name": "4. Voo Store", "lat": 52.5005, "lng": 13.4215, "desc": "패션 피플들의 숨겨진 편집샵"},
        {"name": "5. Landwehr Canal", "lat": 52.4960, "lng": 13.4150, "desc": "운하 산책"}
    ],
    "🛍️ Theme 5: 럭셔리 & 쇼핑": [
        {"name": "1. KaDeWe 백화점", "lat": 52.5015, "lng": 13.3414, "desc": "유럽 최대 백화점"},
        {"name": "2. 쿠담 거리", "lat": 52.5028, "lng": 13.3323, "desc": "베를린의 샹젤리제 명품 거리"},
        {"name": "3. Schwarzes Café", "lat": 52.5060, "lng": 13.3250, "desc": "24시간 영업하는 예술가들의 아지트"},
        {"name": "4. C/O Berlin", "lat": 52.5065, "lng": 13.3325, "desc": "사진 예술 전문 미술관"},
        {"name": "5. Savignyplatz", "lat": 52.5060, "lng": 13.3220, "desc": "고풍스러운 서점과 카페 광장"}
    ],
    "🌙 Theme 6: 화려한 밤": [
        {"name": "1. TV타워", "lat": 52.5208, "lng": 13.4094, "desc": "야경 감상"},
        {"name": "2. 로젠탈러 거리", "lat": 52.5270, "lng": 13.4020, "desc": "트렌디한 골목"},
        {"name": "3. Clärchens Ballhaus", "lat": 52.5265, "lng": 13.3965, "desc": "무도회장 분위기 식사"},
        {"name": "4. Friedrichstadt-Palast", "lat": 52.5235, "lng": 13.3885, "desc": "화려한 쇼 관람"},
        {"name": "5. 브란덴부르크 문", "lat": 52.5163, "lng": 13.3777, "desc": "밤 조명이 켜진 랜드마크"}
    ]
}

# ---------------------------------------------------------
# 4. UI 및 메인 로직
# ---------------------------------------------------------
st.title("🇩🇪 베를린 통합 여행 가이드")
st.caption("2023년 데이터 기반 안전 여행 & 맞춤 코스")

# 세션 초기화
if 'reviews' not in st.session_state: st.session_state['reviews'] = {}
if 'recommendations' not in st.session_state: st.session_state['recommendations'] = []
if 'messages' not in st.session_state: st.session_state['messages'] = []
if 'map_center' not in st.session_state: st.session_state['map_center'] = [52.5200, 13.4050]
if 'search_marker' not in st.session_state: st.session_state['search_marker'] = None

# [상단: 환율 & 날씨] - 클릭 시 그래프 표시
c1, c2 = st.columns(2)
with c1:
    curr_rate, hist_rate = get_exchange_data()
    st.metric("💶 유로 환율 (1 EUR)", f"{curr_rate:.0f}원", delta="실시간")
    with st.expander("📉 1개월 환율 추이 보기"):
        if not hist_rate.empty:
            st.line_chart(hist_rate['Close'])
        else:
            st.write("환율 데이터를 불러올 수 없습니다.")

with c2:
    w_curr, w_fore = get_weather_forecast()
    st.metric("⛅ 베를린 현재 기온", f"{w_curr['temperature']}°C")
    with st.expander("📅 7일 날씨 예보 보기"):
        if not w_fore.empty:
            fig_w = px.line(w_fore, x='Date', y=['Max Temp', 'Min Temp'], title="주간 기온 예측")
            st.plotly_chart(fig_w, use_container_width=True)
        else:
            st.write("날씨 예보를 불러올 수 없습니다.")

st.divider()

# --- 사이드바 ---
st.sidebar.title("🛠️ 여행 도구")

# 검색
st.sidebar.subheader("📍 장소 이동")
search_query = st.sidebar.text_input("지역/장소 검색", placeholder="예: Kreuzberg")
if search_query:
    lat, lng, name = search_location(search_query + " Berlin")
    if lat:
        st.session_state['map_center'] = [lat, lng]
        st.session_state['search_marker'] = {"lat": lat, "lng": lng, "name": name}
        st.sidebar.success(f"이동: {name}")

st.sidebar.divider()

# ★ 지도 필터 (공통)
st.sidebar.subheader("👀 지도 표시 설정")
show_crime = st.sidebar.checkbox("🚨 범죄 위험도 (지역별)", value=True)
st.sidebar.write("---")
show_food = st.sidebar.checkbox("🍽️ 주변 맛집", value=True)
show_hotel = st.sidebar.checkbox("🏨 숙박시설", value=False)
show_tour = st.sidebar.checkbox("📸 관광명소", value=False)

# ★ 음식점 유형 필터 (Tab 1용)
st.sidebar.write("---")
st.sidebar.markdown("**🥘 음식점 유형 (자유탐험 탭)**")
cuisine_options = ["전체", "한식", "양식", "일식", "중식", "아시안", "카페", "기타"]
selected_cuisines = st.sidebar.multiselect("원하는 종류 선택", cuisine_options, default=["전체"])

# 탭 구성
tab1, tab2, tab3, tab4 = st.tabs(["🗺️ 통합 지도", "🚩 추천 코스", "💬 커뮤니티/AI", "📊 범죄 분석"])

# =========================================================
# TAB 1: 통합 지도 (범죄 + 유형별 맛집)
# =========================================================
with tab1:
    center = st.session_state['map_center']
    m = folium.Map(location=center, zoom_start=14)

    # 1. 범죄 데이터 레이어 (엑셀)
    if show_crime:
        crime_df = load_crime_data_excel(CRIME_FILE_NAME)
        if not crime_df.empty:
            geo_url = "https://raw.githubusercontent.com/funkeinteraktiv/Berlin-Geodaten/master/berlin_bezirke.geojson"
            folium.Choropleth(
                geo_data=geo_url, name="범죄 위험도", data=crime_df,
                columns=["District", "Total_Crime"], key_on="feature.properties.name",
                fill_color="YlOrRd", fill_opacity=0.5, line_opacity=0.2,
                legend_name="2023년 총 범죄 발생 수"
            ).add_to(m)

    # 2. 검색 핀
    if st.session_state['search_marker']:
        sm = st.session_state['search_marker']
        folium.Marker([sm['lat'], sm['lng']], popup=sm['name'], icon=folium.Icon(color='red', icon='info-sign')).add_to(m)

    # 3. 장소 마커 (아이콘 적용)
    if show_food:
        places = get_osm_places('restaurant', center[0], center[1], 3000, selected_cuisines)
        fg_food = folium.FeatureGroup(name="맛집")
        for p in places:
            html = f"<div style='width:150px'><b>{p['name']}</b><br><span style='color:grey'>{p['desc']}</span><br><a href='{p['link']}' target='_blank'>구글 검색</a></div>"
            folium.Marker(
                [p['lat'], p['lng']], popup=html, 
                icon=folium.Icon(color='green', icon='cutlery', prefix='fa')
            ).add_to(fg_food)
        fg_food.add_to(m)

    if show_hotel:
        places = get_osm_places('hotel', center[0], center[1])
        fg_hotel = folium.FeatureGroup(name="호텔")
        for p in places:
            html = f"<div style='width:150px'><b>{p['name']}</b><br><span style='color:grey'>{p['desc']}</span><br><a href='{p['link']}' target='_blank'>구글 검색</a></div>"
            folium.Marker(
                [p['lat'], p['lng']], popup=html, 
                icon=folium.Icon(color='blue', icon='bed', prefix='fa')
            ).add_to(fg_hotel)
        fg_hotel.add_to(m)

    if show_tour:
        places = get_osm_places('tourism', center[0], center[1])
        fg_tour = folium.FeatureGroup(name="관광")
        for p in places:
            html = f"<div style='width:150px'><b>{p['name']}</b><br><span style='color:grey'>{p['desc']}</span><br><a href='{p['link']}' target='_blank'>구글 검색</a></div>"
            folium.Marker(
                [p['lat'], p['lng']], popup=html, 
                icon=folium.Icon(color='purple', icon='camera', prefix='fa')
            ).add_to(fg_tour)
        fg_tour.add_to(m)

    st_folium(m, width="100%", height=600)

# =========================================================
# TAB 2: 추천 코스 (자연스러운 레이아웃)
# =========================================================
with tab2:
    st.subheader("🚩 테마별 추천 여행 코스")
    
    themes = list(courses.keys())
    selected_theme = st.radio("테마를 선택하세요:", themes, horizontal=True)
    course_data = courses[selected_theme]
    
    show_crime_course = st.checkbox("🚨 이 지도에도 범죄 위험도 표시", value=False)

    # ★ 레이아웃 개선: 지도(2) : 설명(1) 비율로 조정하여 자연스럽게 붙임
    c_col1, c_col2 = st.columns([2, 1])
    
    with c_col1:
        # 지도
        m2 = folium.Map(location=[course_data[2]['lat'], course_data[2]['lng']], zoom_start=13)
        if show_crime_course:
            crime_df = load_crime_data_excel(CRIME_FILE_NAME)
            if not crime_df.empty:
                folium.Choropleth(
                    geo_data="https://raw.githubusercontent.com/funkeinteraktiv/Berlin-Geodaten/master/berlin_bezirke.geojson",
                    data=crime_df, columns=["District", "Total_Crime"], key_on="feature.properties.name",
                    fill_color="YlOrRd", fill_opacity=0.4, line_opacity=0.2, name="범죄"
                ).add_to(m2)

        points = []
        for i, item in enumerate(course_data):
            loc = [item['lat'], item['lng']]
            points.append(loc)
            icon_name = 'cutlery' if '맛집' in item.get('desc', '') or '음식' in item.get('desc', '') else 'camera'
            icon_color = 'orange' if icon_name == 'cutlery' else 'blue'
            folium.Marker(loc, tooltip=f"{i+1}. {item['name']}", icon=folium.Icon(color=icon_color, icon=icon_name, prefix='fa')).add_to(m2)
        
        folium.PolyLine(points, color="red", weight=4, opacity=0.7).add_to(m2)
        st_folium(m2, height=600, use_container_width=True) # 높이 조절
        
    with c_col2:
        # 설명 (스크롤/접기 없이 바로 보여줌)
        st.markdown(f"### 🚶 {selected_theme}")
        st.write("---")
        for idx, spot in enumerate(course_data):
            st.markdown(f"#### {idx+1}. {spot['name']}")
            st.write(f"📝 {spot['desc']}")
            q = spot['name'].replace(" ", "+") + "+Berlin"
            st.markdown(f"[👉 구글 검색](https://www.google.com/search?q={q})")
            st.write("") # 간격

# =========================================================
# TAB 3: 커뮤니티 & AI (분리형 구조)
# =========================================================
with tab3:
    col_review, col_rec = st.columns(2)
    
    # 1. 장소별 후기
    with col_review:
        st.subheader("💬 장소별 후기 남기기")
        all_places = sorted(list(set([p['name'] for v in courses.values() for p in v])))
        target_place = st.selectbox("장소 선택", ["선택하세요"] + all_places)
        
        if target_place != "선택하세요":
            if target_place not in st.session_state['reviews']:
                st.session_state['reviews'][target_place] = []
                
            with st.form(f"review_{target_place}"):
                rv_text = st.text_area("후기 내용")
                if st.form_submit_button("등록"):
                    st.session_state['reviews'][target_place].append(rv_text)
                    st.rerun()
            
            if st.session_state['reviews'][target_place]:
                st.write("---")
                for rv in st.session_state['reviews'][target_place]:
                    st.info(rv)

    # 2. 나만의 장소 추천
    with col_rec:
        st.subheader("👍 나만의 장소 추천")
        with st.form("rec_form", clear_on_submit=True):
            name = st.text_input("장소 이름")
            reason = st.text_input("추천 이유")
            if st.form_submit_button("추천하기"):
                st.session_state['recommendations'].insert(0, {"place": name, "desc": reason, "replies": []})
                st.rerun()
        
        if st.session_state['recommendations']:
            st.write("---")
            for i, rec in enumerate(st.session_state['recommendations']):
                with st.expander(f"📍 {rec['place']}", expanded=True):
                    st.write(f"📝 {rec['desc']}")
                    for reply in rec['replies']:
                        st.caption(f"↳ {reply}")
                    
                    r_text = st.text_input("댓글", key=f"re_{i}")
                    if st.button("등록", key=f"btn_{i}"):
                        rec['replies'].append(r_text)
                        st.rerun()

    # 3. AI 챗봇
    st.divider()
    st.subheader("🤖 Gemini 여행 비서")
    chat_box = st.container(height=300)
    for msg in st.session_state['messages']:
        chat_box.chat_message(msg['role']).write(msg['content'])
    if prompt := st.chat_input("질문하세요..."):
        st.session_state['messages'].append({"role": "user", "content": prompt})
        chat_box.chat_message("user").write(prompt)
        with chat_box.chat_message("assistant"):
            resp = get_gemini_response(prompt)
            st.write(resp)
        st.session_state['messages'].append({"role": "assistant", "content": resp})

# =========================================================
# TAB 4: 범죄 통계 분석 (한글화 & Interactive)
# =========================================================
with tab4:
    st.header("📊 베를린 범죄 데이터 상세 분석")
    
    df_stat = load_crime_data_excel(CRIME_FILE_NAME)
    
    # 번역 맵핑
    crime_trans = translate_crime_columns(df_stat)
    
    if not df_stat.empty:
        total_crime = df_stat['Total_Crime'].sum()
        max_district = df_stat.loc[df_stat['Total_Crime'].idxmax()]['District']
        
        k1, k2 = st.columns(2)
        k1.metric("분석 대상 총 범죄 수", f"{int(total_crime):,}건")
        k2.metric("최다 발생 구역", max_district)
        
        st.divider()
        
        # 1. 구별 상세 분석 (Interactive Dropdown)
        st.subheader("🔍 구(District)별 상세 분석")
        districts_list = sorted(df_stat['District'].unique())
        selected_district_anal = st.selectbox("분석할 구를 선택하세요", districts_list)
        
        df_district_only = df_stat[df_stat['District'] == selected_district_anal]
        
        crime_cols = [c for c in df_stat.columns if c not in ['District', 'Total_Crime', 'LOR-Schlüssel (Bezirksregion)']]
        
        if crime_cols:
            district_crime_counts = df_district_only[crime_cols].sum().sort_values(ascending=False).head(5)
            # 인덱스(독일어)를 한글로 변환
            district_crime_counts.index = [crime_trans.get(idx, idx) for idx in district_crime_counts.index]
            
            fig_district_bar = px.bar(
                x=district_crime_counts.values,
                y=district_crime_counts.index,
                orientation='h',
                title=f"{selected_district_anal} 지역 TOP 5 범죄 유형",
                labels={'x': '건수', 'y': '범죄 유형'},
                text=district_crime_counts.values,
                color=district_crime_counts.values,
                color_continuous_scale='Reds'
            )
            fig_district_bar.update_layout(yaxis=dict(autorange="reversed"))
            st.plotly_chart(fig_district_bar, use_container_width=True)

        st.divider()
        
        c1, c2 = st.columns(2)
        
        with c1:
            st.subheader("🏙️ 구별 범죄 분포")
            df_sorted = df_stat.sort_values('Total_Crime', ascending=True)
            fig_bar = px.bar(
                df_sorted, x='Total_Crime', y='District', orientation='h',
                text='Total_Crime', 
                color='Total_Crime', color_continuous_scale='Reds'
            )
            fig_bar.update_traces(texttemplate='%{text:.2s}', textposition='outside')
            st.plotly_chart(fig_bar, use_container_width=True)
            
        with c2:
            st.subheader("🥧 전체 범죄 유형 비율")
            if crime_cols:
                type_sums = df_stat[crime_cols].sum().sort_values(ascending=False).head(10)
                # 한글 변환
                type_sums.index = [crime_trans.get(idx, idx) for idx in type_sums.index]
                
                fig_pie = px.pie(
                    values=type_sums.values, names=type_sums.index,
                    title="상위 10개 범죄 유형", hole=0.3
                )
                fig_pie.update_traces(textposition='inside', textinfo='percent+label')
                st.plotly_chart(fig_pie, use_container_width=True)

    else:
        st.warning("데이터를 분석할 수 없습니다. 엑셀 파일을 확인해주세요.")
