import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import requests
import google.generativeai as genai
import googlemaps
import plotly.express as px

# ---------------------------------------------------------
# 1. 설정 및 API 키 로드
# ---------------------------------------------------------
st.set_page_config(layout="wide", page_title="베를린 풀코스 가이드 (OSM + Analysis)")

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
# 2. 데이터 처리 함수
# ---------------------------------------------------------
@st.cache_data
def get_exchange_rate():
    try:
        url = "https://api.exchangerate-api.com/v4/latest/EUR"
        data = requests.get(url).json()
        return data['rates']['KRW']
    except:
        return 1450.0

@st.cache_data
def get_weather():
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=52.52&longitude=13.41&current_weather=true"
        data = requests.get(url).json()
        return data['current_weather']
    except:
        return {"temperature": 15.0, "weathercode": 0}

@st.cache_data
def get_osm_places(category, lat, lng, radius_m=3000, cuisine_filter=None):
    """
    OpenStreetMap 데이터 가져오기 (반경 내 검색)
    """
    overpass_url = "http://overpass-api.de/api/interpreter"
    
    if category == 'restaurant':
        tag = '["amenity"="restaurant"]'
    elif category == 'hotel':
        tag = '["tourism"="hotel"]'
    elif category == 'tourism':
        tag = '["tourism"~"attraction|museum|artwork|viewpoint"]'
    else:
        return []

    # 쿼리: 중심좌표(lat, lng) 주변 radius_m 미터 검색
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
                cuisine = element['tags'].get('cuisine', 'general').lower()
                name = element['tags']['name']
                
                place_type = "관광지"
                if category == 'restaurant':
                    if 'korean' in cuisine: place_type = "한식"
                    elif any(x in cuisine for x in ['burger', 'pizza', 'italian', 'french', 'german', 'american', 'steak']): place_type = "양식"
                    elif any(x in cuisine for x in ['chinese', 'vietnamese', 'thai', 'japanese', 'sushi', 'asian', 'indian']): place_type = "아시안"
                    elif any(x in cuisine for x in ['coffee', 'cafe', 'cake']): place_type = "카페"
                    else: place_type = "식당"
                        
                    if cuisine_filter and "전체" not in cuisine_filter: 
                        if place_type not in cuisine_filter: continue
                elif category == 'hotel':
                    place_type = "숙소"

                search_query = f"{name} Berlin".replace(" ", "+")
                google_link = f"https://www.google.com/search?q={search_query}"

                results.append({
                    "name": name,
                    "lat": element['lat'],
                    "lng": element['lon'],
                    "type": category,
                    "desc": place_type, 
                    "link": google_link
                })
        return results
    except Exception:
        return []

# 지도용 (구별 합계)
@st.cache_data
def load_and_process_crime_data(csv_file):
    try:
        df = pd.read_csv(csv_file, on_bad_lines='skip')
        if 'District' not in df.columns: return pd.DataFrame()
        if 'Year' in df.columns:
            latest_year = df['Year'].max()
            df = df[df['Year'] == latest_year]
        numeric_cols = df.select_dtypes(include=['number']).columns
        cols_to_exclude = ['Year', 'Code', 'District', 'Location', 'lat', 'lng', 'Lat', 'Lng']
        cols_to_sum = [c for c in numeric_cols if c not in cols_to_exclude]
        df['Total_Crime'] = df[cols_to_sum].sum(axis=1)
        district_df = df.groupby('District')['Total_Crime'].sum().reset_index()
        district_df['District'] = district_df['District'].str.strip()
        return district_df
    except: return pd.DataFrame()

# 분석용 (원본 데이터)
@st.cache_data
def load_crime_data_raw(csv_file):
    try:
        df = pd.read_csv(csv_file, on_bad_lines='skip')
        if 'District' not in df.columns: return pd.DataFrame()
        return df
    except: return pd.DataFrame()

def get_gemini_response(prompt):
    if not GEMINI_API_KEY: return "API 키 확인 필요"
    try:
        model = genai.GenerativeModel('gemini-pro')
        response = model.generate_content(prompt)
        return response.text
    except: return "AI 응답 오류"

def search_location(query):
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {'q': query, 'format': 'json', 'limit': 1}
        headers = {'User-Agent': 'BerlinApp/1.0'}
        res = requests.get(url, params=params, headers=headers).json()
        if res:
            return float(res[0]['lat']), float(res[0]['lon']), res[0]['display_name']
    except:
        pass
    return None, None, None

# ---------------------------------------------------------
# 3. 여행 코스 데이터
# ---------------------------------------------------------
courses = {
    "🌳 Theme 1: 숲과 힐링 (티어가르텐)": [
        {"name": "1. 전승기념탑", "lat": 52.5145, "lng": 13.3501, "type": "view", "desc": "베를린 전경이 한눈에 보이는 황금 천사상"},
        {"name": "2. 티어가르텐 산책", "lat": 52.5135, "lng": 13.3575, "type": "walk", "desc": "도심 속 거대한 허파, 맑은 공기 마시기"},
        {"name": "3. Cafe am Neuen See", "lat": 52.5076, "lng": 13.3448, "type": "food", "desc": "호수 바로 앞, 피자와 맥주가 맛있는 비어가든"},
        {"name": "4. 베를린 동물원", "lat": 52.5079, "lng": 13.3377, "type": "view", "desc": "세계 최대 종을 보유한 역사 깊은 동물원"},
        {"name": "5. Monkey Bar", "lat": 52.5049, "lng": 13.3353, "type": "food", "desc": "동물원 원숭이를 내려다보며 칵테일 한잔"},
        {"name": "6. 카이저 빌헬름 교회", "lat": 52.5048, "lng": 13.3350, "type": "view", "desc": "전쟁의 참상을 기억하기 위해 보존된 교회"}
    ],
    "🎨 Theme 2: 예술과 고전 (박물관 섬)": [
        {"name": "1. 베를린 돔", "lat": 52.5190, "lng": 13.4010, "type": "view", "desc": "웅장한 돔 지붕 위에서 보는 시내 뷰"},
        {"name": "2. 구 국립 미술관", "lat": 52.5208, "lng": 13.3982, "type": "view", "desc": "그리스 신전 같은 외관과 19세기 회화"},
        {"name": "3. 제임스 사이먼 공원", "lat": 52.5213, "lng": 13.4005, "type": "walk", "desc": "슈프레 강변에 앉아 쉬어가는 핫플"},
        {"name": "4. Hackescher Hof", "lat": 52.5246, "lng": 13.4020, "type": "view", "desc": "아르누보 양식의 아름다운 8개 안뜰"},
        {"name": "5. Monsieur Vuong", "lat": 52.5244, "lng": 13.4085, "type": "food", "desc": "줄 서서 먹는 베트남 쌀국수 맛집"},
        {"name": "6. Zeit für Brot", "lat": 52.5265, "lng": 13.4090, "type": "food", "desc": "시나몬 롤이 입에서 녹는 베이커리"}
    ],
    "🏰 Theme 3: 분단의 역사 (장벽 투어)": [
        {"name": "1. 베를린 장벽 기념관", "lat": 52.5352, "lng": 13.3903, "type": "view", "desc": "장벽이 실제 모습 그대로 보존된 곳"},
        {"name": "2. Mauerpark", "lat": 52.5404, "lng": 13.4048, "type": "walk", "desc": "일요일 벼룩시장과 가라오케"},
        {"name": "3. Prater Beer Garden", "lat": 52.5399, "lng": 13.4101, "type": "food", "desc": "베를린에서 가장 오래된 야외 맥주집"},
        {"name": "4. 체크포인트 찰리", "lat": 52.5074, "lng": 13.3904, "type": "view", "desc": "분단 시절 검문소"},
        {"name": "5. Topography of Terror", "lat": 52.5065, "lng": 13.3835, "type": "view", "desc": "나치 비밀경찰 본부 터 역사관"},
        {"name": "6. Mall of Berlin", "lat": 52.5106, "lng": 13.3807, "type": "food", "desc": "식사와 쇼핑을 해결하는 대형 몰"}
    ],
    "🕶️ Theme 4: 힙스터 성지 (크로이츠베르크)": [
        {"name": "1. 오버바움 다리", "lat": 52.5015, "lng": 13.4455, "type": "view", "desc": "가장 아름다운 붉은 벽돌 다리"},
        {"name": "2. 이스트 사이드 갤러리", "lat": 52.5050, "lng": 13.4397, "type": "walk", "desc": "형제의 키스 그림이 있는 야외 갤러리"},
        {"name": "3. Burgermeister", "lat": 52.5005, "lng": 13.4420, "type": "food", "desc": "다리 밑 공중화장실을 개조한 힙한 버거집"},
        {"name": "4. Markthalle Neun", "lat": 52.5020, "lng": 13.4310, "type": "food", "desc": "트렌디한 실내 시장과 스트릿 푸드"},
        {"name": "5. Voo Store", "lat": 52.5005, "lng": 13.4215, "type": "view", "desc": "패션 피플들의 숨겨진 편집샵"},
        {"name": "6. Landwehr Canal", "lat": 52.4960, "lng": 13.4150, "type": "walk", "desc": "운하를 따라 걷는 평화로운 산책로"}
    ],
    "🛍️ Theme 5: 럭셔리 & 쇼핑 (쿠담)": [
        {"name": "1. KaDeWe 백화점", "lat": 52.5015, "lng": 13.3414, "type": "view", "desc": "유럽 최대 백화점"},
        {"name": "2. 쿠담 거리", "lat": 52.5028, "lng": 13.3323, "type": "walk", "desc": "베를린의 샹젤리제 명품 거리"},
        {"name": "3. Bikini Berlin", "lat": 52.5055, "lng": 13.3370, "type": "view", "desc": "동물원이 보이는 독특한 쇼핑몰"},
        {"name": "4. C/O Berlin", "lat": 52.5065, "lng": 13.3325, "type": "view", "desc": "사진 예술 전문 미술관"},
        {"name": "5. Schwarzes Café", "lat": 52.5060, "lng": 13.3250, "type": "food", "desc": "24시간 영업하는 예술가들의 아지트"},
        {"name": "6. Savignyplatz", "lat": 52.5060, "lng": 13.3220, "type": "walk", "desc": "고풍스러운 서점과 카페 광장"}
    ],
    "🌙 Theme 6: 화려한 밤 (미테 & 야경)": [
        {"name": "1. TV타워", "lat": 52.5208, "lng": 13.4094, "type": "view", "desc": "베를린 가장 높은 곳에서 야경 감상"},
        {"name": "2. 로젠탈러 거리", "lat": 52.5270, "lng": 13.4020, "type": "walk", "desc": "트렌디한 샵과 갤러리 골목"},
        {"name": "3. Clärchens Ballhaus", "lat": 52.5265, "lng": 13.3965, "type": "food", "desc": "100년 넘은 무도회장에서 식사"},
        {"name": "4. House of Small Wonder", "lat": 52.5240, "lng": 13.3920, "type": "food", "desc": "식물원 같은 인테리어의 브런치"},
        {"name": "5. Friedrichstadt-Palast", "lat": 52.5235, "lng": 13.3885, "type": "view", "desc": "라스베가스 스타일의 화려한 쇼"},
        {"name": "6. 브란덴부르크 문", "lat": 52.5163, "lng": 13.3777, "type": "walk", "desc": "밤 조명이 켜진 랜드마크"}
    ]
}

# ---------------------------------------------------------
# 4. 메인 화면 구성
# ---------------------------------------------------------
st.title("🇩🇪 베를린 풀코스 가이드")
st.caption("핀을 클릭하면 구글 검색으로 이동합니다!")

# 세션 초기화
if 'reviews' not in st.session_state: st.session_state['reviews'] = {}
if 'recommendations' not in st.session_state: st.session_state['recommendations'] = []
if 'messages' not in st.session_state: st.session_state['messages'] = []
if 'map_center' not in st.session_state: st.session_state['map_center'] = [52.5200, 13.4050]
if 'search_marker' not in st.session_state: st.session_state['search_marker'] = None

# [1] 환율 & 날씨
col1, col2 = st.columns(2)
with col1:
    rate = get_exchange_rate()
    st.metric(label="💶 현재 유로 환율", value=f"{rate:.0f}원", delta="1 EUR 기준")
with col2:
    w = get_weather()
    st.metric(label="⛅ 베를린 기온", value=f"{w['temperature']}°C")

st.divider()

# --- 사이드바 ---
st.sidebar.title("🛠️ 여행 도구")

# 1. 검색 (★ 여기가 중요합니다!)
st.sidebar.subheader("🔍 장소 찾기 (위치 이동)")
st.sidebar.caption("다른 지역을 보려면 검색하세요! (예: Kreuzberg)")
search_query = st.sidebar.text_input("장소/지역 이름", placeholder="엔터키를 누르면 이동합니다")
if search_query:
    lat, lng, name = search_location(search_query + " Berlin")
    if lat and lng:
        st.session_state['map_center'] = [lat, lng]
        st.session_state['search_marker'] = {"lat": lat, "lng": lng, "name": name}
        st.sidebar.success(f"이동 완료: {name}")
    else:
        st.sidebar.error("장소를 찾을 수 없습니다.")

st.sidebar.divider()

# 2. 필터
st.sidebar.subheader("🗺️ 지도 필터")
show_crime = st.sidebar.toggle("🚨 범죄 위험도 보기", True)
show_hotel = st.sidebar.toggle("🏨 숙박시설 (Hotel)", False)
show_tour = st.sidebar.toggle("📸 관광지 (Tourism)", False)

st.sidebar.markdown("**🍽️ 음식점 종류 선택**")
cuisine_options = ["전체", "한식", "양식", "아시안", "카페", "일반/기타"]
selected_cuisines = st.sidebar.multiselect("원하는 종류를 선택하세요", cuisine_options, default=["전체"])

# --- 메인 탭 ---
tab1, tab2, tab3, tab4 = st.tabs(["🗺️ 자유 탐험", "🚩 추천 코스 (6 Themes)", "💬 여행자 수다방", "📊 범죄 분석"])

# =========================================================
# TAB 1: 자유 탐험 (검색 중심좌표 반영)
# =========================================================
with tab1:
    # ★ 현재 지도 중심(검색한 위치)을 가져옵니다.
    center = st.session_state['map_center']
    m1 = folium.Map(location=center, zoom_start=14) # 줌 레벨 조정

    if st.session_state['search_marker']:
        sm = st.session_state['search_marker']
        folium.Marker(
            [sm['lat'], sm['lng']], 
            popup=sm['name'],
            icon=folium.Icon(color='red', icon='info-sign')
        ).add_to(m1)

    # 1. 범죄 지도
    if show_crime:
        crime_df = load_and_process_crime_data("Berlin_crimes.csv")
        if not crime_df.empty:
            folium.Choropleth(
                geo_data="https://raw.githubusercontent.com/funkeinteraktiv/Berlin-Geodaten/master/berlin_bezirke.geojson",
                data=crime_df,
                columns=["District", "Total_Crime"],
                key_on="feature.properties.name",
                fill_color="YlOrRd",
                fill_opacity=0.4,
                line_opacity=0.2,
                name="범죄"
            ).add_to(m1)

    # 2. 음식점 (중심 좌표 주변 검색)
    if selected_cuisines:
        # ★ center[0], center[1]을 사용해 현재 보고 있는 지역 주변을 긁어옵니다.
        places = get_osm_places('restaurant', center[0], center[1], 3000, selected_cuisines)
        fg_food = folium.FeatureGroup(name="식당")
        for p in places:
            c_color = 'green'
            if p['desc'] == '한식': c_color = 'red'
            elif p['desc'] == '카페': c_color = 'beige'
            
            popup_html = f"""
            <div style="font-family:sans-serif; width:150px">
                <b>{p['name']}</b><br>
                <span style="
