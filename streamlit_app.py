import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import requests
import google.generativeai as genai
import googlemaps

# ---------------------------------------------------------
# 1. 설정 및 API 키 로드
# ---------------------------------------------------------
st.set_page_config(layout="wide", page_title="베를린 풀코스 가이드")

GMAPS_API_KEY = st.secrets.get("google_maps_api_key", "")
GEMINI_API_KEY = st.secrets.get("gemini_api_key", "")

# 클라이언트 초기화
gmaps = None
if GMAPS_API_KEY:
    try:
        gmaps = googlemaps.Client(key=GMAPS_API_KEY)
    except:
        pass

if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
    except:
        pass

# ---------------------------------------------------------
# 2. 데이터 처리 함수 (수정됨)
# ---------------------------------------------------------
@st.cache_data
def get_osm_places(category, lat, lng, radius_m=2000):
    """
    OpenStreetMap을 이용해 식당, 호텔, 관광지 데이터를 제한 없이 가져옵니다.
    """
    overpass_url = "http://overpass-api.de/api/interpreter"
    
    # 태그 설정 (호텔 추가됨)
    if category == 'restaurant':
        tag = '["amenity"="restaurant"]'
    elif category == 'hotel':
        tag = '["tourism"="hotel"]'
    elif category == 'tourism':
        tag = '["tourism"~"attraction|museum|artwork|viewpoint"]'
    else:
        return []

    query = f"""
    [out:json];
    (
      node{tag}(around:{radius_m},{lat},{lng});
    );
    out body;
    """
    
    try:
        response = requests.get(overpass_url, params={'data': query})
        data = response.json()
        
        results = []
        for element in data['elements']:
            if 'tags' in element and 'name' in element['tags']:
                results.append({
                    "name": element['tags']['name'],
                    "lat": element['lat'],
                    "lng": element['lon'],
                    "type": category,
                    "cuisine": element['tags'].get('cuisine', '')
                })
        return results
    except Exception:
        return []

@st.cache_data
def load_and_process_crime_data(csv_file):
    """
    범죄 데이터를 읽어오고 지도와 매칭되도록 구 이름을 정리합니다.
    """
    try:
        # 파일 읽기
        df = pd.read_csv(csv_file, on_bad_lines='skip')
        
        # 필수 컬럼 확인
        if 'District' not in df.columns:
            return pd.DataFrame()

        # 최신 연도만 필터링
        if 'Year' in df.columns:
            latest_year = df['Year'].max()
            df = df[df['Year'] == latest_year]
        
        # 숫자 컬럼 합산 (Total_Crime 생성)
        numeric_cols = df.select_dtypes(include=['number']).columns
        cols_to_sum = [c for c in numeric_cols if c not in ['Year', 'Code', 'District', 'Location']]
        df['Total_Crime'] = df[cols_to_sum].sum(axis=1)
        
        # 구별 합계 계산
        district_df = df.groupby('District')['Total_Crime'].sum().reset_index()

        # ★중요★ GeoJSON과 이름 매칭을 위해 공백 제거 및 이름 통일
        # 베를린 GeoJSON은 보통 "Mitte", "Friedrichshain-Kreuzberg" 등으로 되어 있음
        district_df['District'] = district_df['District'].str.strip() 
        
        # 혹시 모를 매칭 오류를 위해 이름 수정 (필요시 추가)
        # 예: 'Charlottenb-Wilm.' -> 'Charlottenburg-Wilmersdorf' 
        # (업로드해주신 파일은 이름이 정확해 보여서 strip만 해도 될 것 같습니다)
        
        return district_df
    except Exception as e:
        print(f"Error: {e}")
        return pd.DataFrame()

def get_gemini_response(prompt):
    if not GEMINI_API_KEY: return "API 키 설정을 확인해주세요."
    try:
        model = genai.GenerativeModel('gemini-pro')
        response = model.generate_content(prompt)
        return response.text
    except: return "AI 서버 응답 오류"

# ---------------------------------------------------------
# 3. 데이터 정의 (코스)
# ---------------------------------------------------------
courses = {
    "🌳 Theme 1: 숲과 힐링 (티어가르텐)": [
        {"name": "1. 전승기념탑 (Siegessäule)", "lat": 52.5145, "lng": 13.3501, "type": "view", "desc": "베를린 전경이 한눈에 보이는 황금 천사상"},
        {"name": "2. 티어가르텐 산책", "lat": 52.5135, "lng": 13.3575, "type": "walk", "desc": "도심 속 거대한 허파, 맑은 공기 마시기"},
        {"name": "3. Cafe am Neuen See", "lat": 52.5076, "lng": 13.3448, "type": "food", "desc": "호수 바로 앞, 피자와 맥주가 맛있는 비어가든"},
        {"name": "4. 베를린 동물원/수족관", "lat": 52.5079, "lng": 13.3377, "type": "view", "desc": "세계 최대 종을 보유한 역사 깊은 동물원"},
        {"name": "5. Monkey Bar", "lat": 52.5049, "lng": 13.3353, "type": "food", "desc": "동물원 원숭이를 내려다보며 칵테일 한잔 (25hours 호텔 루프탑)"},
        {"name": "6. 카이저 빌헬름 교회", "lat": 52.5048, "lng": 13.3350, "type": "view", "desc": "전쟁의 참상을 기억하기 위해 부서진 채 보존된 교회"}
    ],
    "🎨 Theme 2: 예술과 고전 (박물관 섬)": [
        {"name": "1. 베를린 돔", "lat": 52.5190, "lng": 13.4010, "type": "view", "desc": "웅장한 돔 지붕 위에서 보는 시내 뷰"},
        {"name": "2. 구 국립 미술관", "lat": 52.5208, "lng": 13.3982, "type": "view", "desc": "그리스 신전 같은 외관과 19세기 회화"},
        {"name": "3. 제임스 사이먼 공원", "lat": 52.5213, "lng": 13.4005, "type": "walk", "desc": "슈프레 강변에 앉아 쉬어가는 현지인 핫플"},
        {"name": "4. Hackescher Hof", "lat": 52.5246, "lng": 13.4020, "type": "view", "desc": "아르누보 양식의 아름다운 8개 안뜰"},
        {"name": "5. Monsieur Vuong", "lat": 52.5244, "lng": 13.4085, "type": "food", "desc": "항상 줄 서서 먹는 전설적인 베트남 쌀국수 맛집"},
        {"name": "6. Zeit für Brot", "lat": 52.5265, "lng": 13.4090, "type": "food", "desc": "시나몬 롤(Schnecke)이 입에서 녹는 베이커리"}
    ],
    "🏰 Theme 3: 분단의 역사 (장벽 투어)": [
        {"name": "1. 베를린 장벽 기념관", "lat": 52.5352, "lng": 13.3903, "type": "view", "desc": "장벽이 실제 모습 그대로 보존된 야외 박물관"},
        {"name": "2. Mauerpark (마우어파크)", "lat": 52.5404, "lng": 13.4048, "type": "walk", "desc": "일요일엔 거대한 벼룩시장과 가라오케가 열림"},
        {"name": "3. Prater Beer Garden", "lat": 52.5399, "lng": 13.4101, "type": "food", "desc": "베를린에서 가장 오래된 야외 맥주집"},
        {"name": "4. 체크포인트 찰리", "lat": 52.5074, "lng": 13.3904, "type": "view", "desc": "미군과 소련군이 대치했던 검문소"},
        {"name": "5. Topography of Terror", "lat": 52.5065, "lng": 13.3835, "type": "view", "desc": "나치 비밀경찰 본부 터에 지어진 무료 역사관"},
        {"name": "6. Mall of Berlin", "lat": 52.5106, "lng": 13.3807, "type": "food", "desc": "역사 투어 후 쇼핑과 식사를 해결하는 대형 몰"}
    ],
    "🕶️ Theme 4: 힙스터 성지 (크로이츠베르크)": [
        {"name": "1. 오버바움 다리", "lat": 52.5015, "lng": 13.4455, "type": "view", "desc": "동서를 잇는 붉은 벽돌 다리, 최고의 포토존"},
        {"name": "2. 이스트 사이드 갤러리", "lat": 52.5050, "lng": 13.4397, "type": "walk", "desc": "형제의 키스 그림이 있는 세계 최장 야외 갤러리"},
        {"name": "3. Burgermeister", "lat": 52.5005, "lng": 13.4420, "type": "food", "desc": "다리 밑 공중화장실을 개조해 만든 힙한 버거집"},
        {"name": "4. Markthalle Neun", "lat": 52.5020, "lng": 13.4310, "type": "food", "desc": "목요일엔 스트릿 푸드 마켓이 열리는 실내 시장"},
        {"name": "5. Voo Store", "lat": 52.5005, "lng": 13.4215, "type": "view", "desc": "패션 피플들이 찾는 숨겨진 편집샵"},
        {"name": "6. Landwehr Canal", "lat": 52.4960, "lng": 13.4150, "type": "walk", "desc": "백조를 보며 걷거나 보트를 타는 운하 산책로"}
    ],
    "🛍️ Theme 5: 럭셔리 & 쇼핑 (쿠담)": [
        {"name": "1. KaDeWe 백화점", "lat": 52.5015, "lng": 13.3414, "type": "view", "desc": "유럽 대륙 최대의 백화점, 6층 식품관 필수"},
        {"name": "2. 쿠담 거리", "lat": 52.5028, "lng": 13.3323, "type": "walk", "desc": "베를린의 샹젤리제, 명품 브랜드 거리"},
        {"name": "3. Bikini Berlin", "lat": 52.5055, "lng": 13.3370, "type": "view", "desc": "동물원이 보이는 독특한 컨셉의 쇼핑몰"},
        {"name": "4. C/O Berlin", "lat": 52.5065, "lng": 13.3325, "type": "view", "desc": "사진 예술 전문 미술관"},
        {"name": "5. Schwarzes Café", "lat": 52.5060, "lng": 13.3250, "type": "food", "desc": "24시간 영업하는 예술가들의 아지트 카페"},
        {"name": "6. Savignyplatz", "lat": 52.5060, "lng": 13.3220, "type": "walk", "desc": "고풍스러운 서점과 레스토랑이 많은 광장"}
    ],
    "🌙 Theme 6: 화려한 밤 (미테 & 야경)": [
        {"name": "1. 알렉산더 광장 TV타워", "lat": 52.5208, "lng": 13.4094, "type": "view", "desc": "베를린 가장 높은 곳에서 야경 감상"},
        {"name": "2. 로젠탈러 거리", "lat": 52.5270, "lng": 13.4020, "type": "walk", "desc": "트렌디한 샵과 갤러리가 모인 골목"},
        {"name": "3. Clärchens Ballhaus", "lat": 52.5265, "lng": 13.3965, "type": "food", "desc": "100년 넘은 무도회장에서 식사 (분위기 최고)"},
        {"name": "4. House of Small Wonder", "lat": 52.5240, "lng": 13.3920, "type": "food", "desc": "식물원 같은 인테리어의 유명 브런치/디너"},
        {"name": "5. Friedrichstadt-Palast", "lat": 52.5235, "lng": 13.3885, "type": "view", "desc": "라스베가스 스타일의 화려한 쇼 공연장"},
        {"name": "6. 브란덴부르크 문 (야경)", "lat": 52.5163, "lng": 13.3777, "type": "walk", "desc": "밤에 조명이 켜지면 더 웅장한 랜드마크"}
    ]
}

# ---------------------------------------------------------
# 4. 메인 화면 구성
# ---------------------------------------------------------
st.title("🇩🇪 베를린 풀코스 가이드")

if 'reviews' not in st.session_state: st.session_state['reviews'] = {}
if 'messages' not in st.session_state: st.session_state['messages'] = []

tab1, tab2, tab3 = st.tabs(["🗺️ 자유 탐험 (호텔/맛집)", "🚩 추천 코스 (6 Themes)", "💬 여행자 수다방"])

# =========================================================
# TAB 1: 자유 탐험 (OSM 데이터 - 호텔 추가됨!)
# =========================================================
with tab1:
    col_a, col_b = st.columns([1, 3])
    with col_a:
        st.subheader("지도 필터")
        st.caption("체크하면 베를린 전체 데이터를 가져옵니다.")
        show_crime = st.toggle("🚨 범죄 위험도 (지역별 색상)", True)
        show_food = st.toggle("🍽️ 식당 (Restaurant)", True)
        show_hotel = st.toggle("🏨 숙박시설 (Hotel)", False) # <--- 호텔 추가됨!
        show_tour = st.toggle("📸 관광지 (Tourism)", False)
        
    with col_b:
        m1 = folium.Map(location=[52.5200, 13.4050], zoom_start=13)

        # 1. 범죄 지도
        if show_crime:
            crime_df = load_and_process_crime_data("Berlin_crimes.csv")
            
            if not crime_df.empty:
                # 베를린 행정구역 GeoJSON
                geo_url = "https://raw.githubusercontent.com/funkeinteraktiv/Berlin-Geodaten/master/berlin_bezirke.geojson"
                
                folium.Choropleth(
                    geo_data=geo_url,
                    data=crime_df,
                    columns=["District", "Total_Crime"],
                    key_on="feature.properties.name", # GeoJSON의 구 이름 속성과 매칭
                    fill_color="YlOrRd",
                    fill_opacity=0.5,
                    line_opacity=0.2,
                    legend_name="범죄 발생 건수"
                ).add_to(m1)
            else:
                st.warning("범죄 데이터 파일을 읽지 못했습니다. 파일명(Berlin_crimes.csv)을 확인하세요.")

        # 2. OSM 데이터 (식당, 호텔, 관광지)
        # 중심 좌표 주변 4km 검색
        center_lat, center_lng = 52.5200, 13.4050
        
        if show_food:
            places = get_osm_places('restaurant', center_lat, center_lng, 4000)
            fg = folium.FeatureGroup(name="식당")
            for p in places:
                popup_html = f"<b>{p['name']}</b><br>{p['cuisine']}"
                folium.CircleMarker(
                    [p['lat'], p['lng']], radius=4, color='green', fill=True, popup=popup_html
                ).add_to(fg)
            fg.add_to(m1)
            
        if show_hotel: # <--- 호텔 로직 추가됨
            places = get_osm_places('hotel', center_lat, center_lng, 4000)
            fg = folium.FeatureGroup(name="호텔")
            for p in places:
                folium.Marker(
                    [p['lat'], p['lng']], 
                    popup=p['name'],
                    icon=folium.Icon(color='blue', icon='bed', prefix='fa')
                ).add_to(fg)
            fg.add_to(m1)

        if show_tour:
            places = get_osm_places('tourism', center_lat, center_lng, 4000)
            fg = folium.FeatureGroup(name="관광")
            for p in places:
                folium.CircleMarker(
                    [p['lat'], p['lng']], radius=5, color='purple', fill=True, popup=p['name']
                ).add_to(fg)
            fg.add_to(m1)

        st_folium(m1, width="100%", height=600)

# =========================================================
# TAB 2: 추천 코스
# =========================================================
with tab2:
    st.subheader("🌟 테마별 추천 코스")
    theme_names = list(courses.keys())
    selected_theme = st.radio("테마 선택:", theme_names, horizontal=True)
    c_data = courses[selected_theme]
    
    c_col1, c_col2 = st.columns([1.5, 1])
    
    with c_col1:
        m2 = folium.Map(location=[c_data[2]['lat'], c_data[2]['lng']], zoom_start=13)
        points = []
        for i, item in enumerate(c_data):
            loc = [item['lat'], item['lng']]
            points.append(loc)
            color = 'orange' if item['type'] == 'food' else 'blue'
            icon = 'cutlery' if item['type'] == 'food' else 'camera'
            folium.Marker(
                loc, popup=item['name'], tooltip=f"{i+1}. {item['name']}",
                icon=folium.Icon(color=color, icon=icon)
            ).add_to(m2)
        folium.PolyLine(points, color="red", weight=4, opacity=0.7).add_to(m2)
        st_folium(m2, width="100%", height=500)
        
    with c_col2:
        st.markdown(f"### {selected_theme}")
        st.markdown("---")
        for item in c_data:
            icon_str = "🍽️" if item['type'] == 'food' else "📸" if item['type'] == 'view' else "🚶"
            with st.expander(f"{icon_str} {item['name']}", expanded=True):
                st.write(f"_{item['desc']}_")
                q = item['name'].replace(" ", "+") + "+Berlin"
                st.markdown(f"[🔍 구글 검색](https://www.google.com/search?q={q})")

# =========================================================
# TAB 3: 수다방 & AI
# =========================================================
with tab3:
    col_chat, col_ai = st.columns([1, 1])
    
    with col_chat:
        st.subheader("💬 여행자 수다방")
        all_places = sorted(list(set([p['name'].split(". ")[1] for v in courses.values() for p in v])))
        sel_place = st.selectbox("장소 선택", all_places)
        
        if sel_place not in st.session_state['reviews']:
            st.session_state['reviews'][sel_place] = []

        with st.form("msg_form", clear_on_submit=True):
            txt = st.text_input("내용 입력")
            if st.form_submit_button("등록"):
                st.session_state['reviews'][sel_place].append(txt)
                st.rerun()
        
        st.write("---")
        for i, msg in enumerate(st.session_state['reviews'][sel_place]):
            c1, c2 = st.columns([8, 1])
            c1.info(f"🗣️ {msg}")
            if c2.button("🗑️", key=f"del_{sel_place}_{i}"):
                del st.session_state['reviews'][sel_place][i]
                st.rerun()

    with col_ai:
        st.subheader("🤖 Gemini 가이드")
        chat_area = st.container(height=400)
        for msg in st.session_state['messages']:
            chat_area.chat_message(msg['role']).write(msg['content'])
        if prompt := st.chat_input("질문하세요..."):
            st.session_state['messages'].append({"role": "user", "content": prompt})
            chat_area.chat_message("user").write(prompt)
            with chat_area.chat_message("assistant"):
                resp = get_gemini_response(prompt)
                st.write(resp)
            st.session_state['messages'].append({"role": "assistant", "content": resp})
