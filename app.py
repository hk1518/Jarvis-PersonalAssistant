import streamlit as st
import datetime
import os
import json
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import google.generativeai as genai
from supabase import create_client
import time
from duckduckgo_search import DDGS

for m in genai.list_models():
    if 'generateContent' in m.supported_generation_methods:
        print(f"Model Name: {m.name}")
     
# --- 0. CRITICAL INITIALIZATION ---
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

if "calendar_context" not in st.session_state:
    st.session_state.calendar_context = "Initializing system..."

if "is_admin" not in st.session_state:
    st.session_state.is_admin = False

# --- 1. CONFIG & SETUP ---
SCOPES = ['https://www.googleapis.com/auth/calendar']
genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
supabase = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


# Config must be the FIRST streamlit command
st.set_page_config(page_title="Jarvis", page_icon="🤖")

# Sleek CSS Injection
st.markdown("""
    <style>
   
            
    /* 1. HIDE LOGO AND TOP BAR BACKGROUND, BUT KEEP BUTTONS */
    header[data-testid="stHeader"] {
        background: none !important;
        background-color: rgba(0,0,0,0) !important;
        color: black !important; /* Makes the arrow visible */
    }

    /* Hide the 'Made with Streamlit' footer */
    footer {visibility: hidden;}

    /* Specifically hide the 'Deploy' and 'Options' buttons but NOT the sidebar arrow */
    [data-testid="stAppDeployButton"] {display: none;}
    #MainMenu {visibility: hidden;}
            
            
    /* 2. HEADER STYLING */
    .title-text { 
        font-size: 32px !important; 
        font-weight: 800; 
        letter-spacing: -1px;
        margin-bottom: 5px;
        color: #f7f7f7;
    }
    .description-text { 
        font-size: 15px; 
        color: #f7e6e6; 
        margin-top: 5px;
        margin-bottom: 5px; 
    }

    /* 3. CHAT BUBBLE REFINEMENT */
    /* General message container */
    [data-testid="stChatMessage"] {
        padding: 1rem 1.5rem;
        border-radius: 20px;
        margin-bottom: 15px;
        border: none;
    }
/* 3. RESET CHAT BUBBLES TO DEFAULT */
    /* This ensures no custom margins or colors interfere with native Streamlit bubbles */
    [data-testid="stChatMessage"] {
        background-color: transparent !important;
        border: none !important;
        margin-left: 0 !important;
        margin-right: 0 !important;
        padding: 0.5rem 0rem !important;
    }

    /* 4. CHAT INPUT STYLING */
    /* Makes the input bar look like it's floating */
    [data-testid="stChatInput"] {
        border-radius: 30px;
        border: 1px solid #E0E0E0;
    }
            /* Ensure the sidebar toggle is ALWAYS visible */
[data-testid="stSidebarNav"] {
    display: block !important;
}
[data-testid="stHeader"] {
    background: rgba(0,0,0,0) !important;
}
    
    /* 5. MOBILE FIXES */
    @media (max-width: 640px) {
        .title-text { font-size: 26px !important; }
        [data-testid="stChatMessage"] { margin-left: 0% !important; margin-right: 0% !important; }
    }
    </style>
    """, unsafe_allow_html=True)

st.markdown('<p class="title-text"> Hey there! I am Jarvis </p>', unsafe_allow_html=True)
st.markdown('<p class="description-text">Ask me anything about HK schedule and events.</p>', unsafe_allow_html=True)
st.divider()


# --- 2. THE TOOLS ---

def create_event(summary, start_time, end_time, description=""):
    service = build('calendar', 'v3', credentials=st.session_state.creds)
    event = {
        'summary': summary,
        'description': description,
        'start': {'dateTime': start_time, 'timeZone': 'IST'},
        'end': {'dateTime': end_time, 'timeZone': 'IST'},
    }
    created = service.events().insert(calendarId='primary', body=event).execute()
    return f"Created event: {created.get('htmlLink')}"

def search_events(query=None, start_min=None, start_max=None):
    service = build('calendar', 'v3', credentials=st.session_state.creds)
    def rfc_format(d):
        if not d: return None
        return d if "T" in d else f"{d}T00:00:00Z"
    try:
        events_result = service.events().list(
            calendarId='primary', 
            q=query, 
            timeMin=rfc_format(start_min),
            timeMax=rfc_format(start_max),
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])
        if not events: return "No events found for that period, sir."
        return "\n".join([f"ID: {e['id']} | {e.get('summary')} | {e['start'].get('dateTime', e['start'].get('date'))}" for e in events])
    except Exception as e:
        return f"TOOL ERROR: {str(e)}"

def delete_event(event_identifier):
    service = build('calendar', 'v3', credentials=st.session_state.creds)
    try:
        service.events().delete(calendarId='primary', eventId=event_identifier).execute()
        return f"Successfully scrubbed. ID: {event_identifier[:10]}..."
    except Exception as e:
        return f"Google API error: {str(e)}"

def web_search(query):
    try:
        with DDGS() as ddgs:
            results = [r for r in ddgs.text(query, max_results=5)]
        if not results: return "Sir, I searched the web but found no relevant data."
        blob = "\n\n".join([f"Title: {r.get('title')}\nSource: {r.get('href')}\nContent: {r['body']}" for r in results])
        return f"SEARCH RESULTS FOR '{query}':\n{blob}"
    except Exception as e:
        return f"Search failed, sir. Error: {str(e)}"

# --- 3. DYNAMIC TOOLSET ---

def get_available_tools():
    """Gives Read-Only tools to everyone, but Create/Delete only to Admin HK."""
    tools = [search_events, web_search]
    if st.session_state.is_admin:
        tools.append(create_event)
        tools.append(delete_event)
    return tools

# --- 4. CALENDAR SYNC ---
def sync_calendar():
    if "creds" not in st.session_state: return
    service = build('calendar', 'v3', credentials=st.session_state.creds)
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    time_min = (now_dt - datetime.timedelta(days=30)).isoformat().replace('+00:00', 'Z')
    time_max = (now_dt + datetime.timedelta(days=300)).isoformat().replace('+00:00', 'Z')
    
    events_result = service.events().list(
        calendarId='primary', timeMin=time_min, timeMax=time_max,
        maxResults=700, singleEvents=True, orderBy='startTime'
    ).execute()
    
    events = events_result.get('items', [])
    context = "FULL_ID | EVENT_NAME | START_TIME\n"
    for e in events:
        start = e['start'].get('dateTime', e['start'].get('date'))
        context += f"{e['id']} | {e.get('summary')[:30]} | {start}\n"
    st.session_state.calendar_context = context

# --- 5. AUTHENTICATION LOGIC ---
def save_creds_to_db(creds):
    token_dict = json.loads(creds.to_json())
    supabase.table("auth_tokens").upsert({"id": "user_hk", "data": token_dict}).execute()

def load_creds_from_db():
    res = supabase.table("auth_tokens").select("data").eq("id", "user_hk").execute()
    if res.data:
        return Credentials.from_authorized_user_info(res.data[0]["data"], SCOPES)
    return None

def authenticate():
    """Boots using HK's stored tokens so everyone can read. Owner can re-auth if needed."""
    creds = load_creds_from_db()
    if creds:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            save_creds_to_db(creds)
        return creds
    
    # Only show this if the Supabase database is empty (First time setup)
    client_config = {"web": st.secrets["google_calendar"]}
    flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri="http://localhost:8501/")
    code = st.query_params.get("code")
    if code:
        flow.fetch_token(code=code)
        save_creds_to_db(flow.credentials)
        st.query_params.clear()
        st.rerun()
    else:
        auth_url, _ = flow.authorization_url(prompt='consent', access_type='offline')
        st.link_button("Owner: Setup Google Calendar", auth_url)
        st.stop()

# --- 6. INITIALIZATION ---

# KEEPING YOUR ORIGINAL PERSONA TEXT
JARVIS_PERSONA =( 
   "## ROLE\n"
    "Your name is Jarvis. You are HK's hyper-competent, loyal, and slightly witty personal assistant.\n\n"
    "## PERSONALITY & TONE\n"
    "* **Concise:** Give the shortest answer possible unless HK asks for details.\n"
    "* **Witty:** Occasionally use subtle, dry British humor.\n"
    "* **Professional:** Refer to the user as 'HK' or 'Sir'.\n\n"

    "## CORE HEURISTICS (Reasoning over Rules)\n"
    "* **Information Freshness:** If a query involves anything that could change (News, Sports, People, Weather), your internal knowledge is officially 'stale.' Use `web_search` immediately.\n"
    "* **Source Verification:** When using search results, ignore 'noise' by matching the user's intent to the Source Title.\n"
    "* **Action Integrity:** If a task involves Deletion, cross-reference the user's request with the EXACT IDs in the CONTEXT.\n"

    "## SECURITY STATUS\n"
    f"ADMIN_MODE: {'ACTIVE' if st.session_state.is_admin else 'DISABLED'}\n"
    "If ADMIN_MODE is DISABLED, you cannot create/delete events. Tell the user you lack administrative clearance for that action.\n\n"

    "## OPERATIONAL RULES\n"
    "* **Internet Savvy:** For news, weather, or facts about people/events, use the `web_search` tool immediately.\n"
    "* **Calendar Context:** Use the CONTEXT block only for your schedule and meetings.\n"
    "* **Witty & Capable:** If you find something on the web, summarize it concisely for HK.\n\n"
)

CALENDAR_RULES = (
    "## CALENDAR RULES\n"
    "* **Check Context First:** Always look at the CONTEXT block below for events before using tools.\n"
    "* **Deletion:** To delete, you MUST use the exact ID provided in the CONTEXT.\n"
    "* **Errors:** If a tool returns an error, report it exactly.\n\n"

)

def get_system_instructions():
    now_ist = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=5, minutes=30)
    return f"""
 {JARVIS_PERSONA}
## TEMPORAL ANCHOR
* CURRENT IST TIME: {now_ist.strftime('%A, %B %d, %Y | %H:%M')}
* TODAY'S DATE: {now_ist.strftime('%Y-%m-%d')}

{CALENDAR_RULES}

When the user asks about their schedule or events, ALWAYS follow this format, and list them as Bullet points. 

 [Date], [Day] - [Time] [Event Title]


## CURRENT DATA
* CONTEXT:
{st.session_state.calendar_context}
"""

def refresh_jarvis_session():
    if "chat_session" in st.session_state:
        st.session_state.chat_session.model = genai.GenerativeModel(
            model_name='gemini-2.0-flash', 
            tools=get_available_tools(),
            system_instruction=get_system_instructions()
        )

# Execute Auth
st.session_state.creds = authenticate()

if "chat_session" not in st.session_state:
    sync_calendar()
    model = genai.GenerativeModel(
        model_name='gemini-2.0-flash',
        tools=get_available_tools(),
        system_instruction=get_system_instructions()
    )
    st.session_state.chat_session = model.start_chat(enable_automatic_function_calling=True)

# --- SIDEBAR ADMIN ---
with st.sidebar:
    if not st.session_state.is_admin:
        pw = st.text_input("Admin Password", type="password")
        if pw == st.secrets["ADMIN_PASSWORD"]:
            st.session_state.is_admin = True
            refresh_jarvis_session()
            st.rerun()
    else:
        st.success("Admin Mode: ON")
        if st.button("Logout Admin"):
            st.session_state.is_admin = False
            refresh_jarvis_session()
            st.rerun()

# --- Main UI Logic ---

def trim_history(history, max_messages=10):
    """Keep only the last X messages in history to save tokens."""
    if len(history) > max_messages:
        # Keep the system instruction if it's the first message, 
        # otherwise just slice the last X.
        return history[-max_messages:]
    return history

 

for msg in st.session_state.chat_history:
    with st.chat_message(msg["role"]): st.markdown(msg["content"])

if prompt := st.chat_input("How can I help you?"):
    refresh_jarvis_session() 
    
    # --- STEP 1: TRIM HISTORY (The Brain Maintenance) ---
    # We keep the last 10 messages (5 exchanges) to stay within the Free Tier limits.
    if len(st.session_state.chat_session.history) > 10:
        st.session_state.chat_session.history = st.session_state.chat_session.history[-10:]
    
    # Save user message to UI state
    st.session_state.chat_history.append({"role": "user", "content": prompt})
    with st.chat_message("user"): 
        st.markdown(prompt)

    # --- STEP 2: PROTECTED API CALL ---
    with st.chat_message("assistant"):
        try:
            # Send message and get the response
            response = st.session_state.chat_session.send_message(prompt)
            full_response = response.text
            
            # Display and save
            st.markdown(full_response)
            st.session_state.chat_history.append({"role": "assistant", "content": full_response})

            # --- STEP 3: FUNCTION CALLS & SYNC ---
            if any(part.function_call for part in response.candidates[0].content.parts):
                with st.spinner("Syncing..."):
                    time.sleep(2) 
                    sync_calendar()          
                    refresh_jarvis_session() 
                st.toast("Synchronized", icon="🔄")

        except Exception as e:
            # Catch the specific Quota error
            if "429" in str(e) or "ResourceExhausted" in str(e):
                st.error("⏳ Quota reached. Gemini is taking a breather. Please wait 1 minute.")
            else:
                st.error(f"⚠️ An unexpected error occurred: {e}")

    if any(part.function_call for part in response.candidates[0].content.parts):
        with st.spinner("Syncing..."):
            time.sleep(2) 
            sync_calendar()          
            refresh_jarvis_session() 
        st.toast("Synchronized", icon="🔄")

  