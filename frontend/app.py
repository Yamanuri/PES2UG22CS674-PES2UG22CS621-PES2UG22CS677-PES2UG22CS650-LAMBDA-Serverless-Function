import streamlit as st
import requests
import json

BASE_URL = "http://localhost:8000"  # URL of your FastAPI backend

st.title("⚡ Lambda Serverless Platform")

page = st.sidebar.selectbox("Navigate", [
    "Deploy Function", "Manage Functions", "Run Function", "Function Metrics", "Monitoring Dashboard"
])

# ---------- Deploy Function ----------
if page == "Deploy Function":
    st.header("📤 Deploy a Function")
    name = st.text_input("Function Name")
    route = st.text_input("Route (for reference only)")
    language = st.selectbox("Language", ["python", "node"])
    timeout = st.number_input("Timeout (seconds)", value=5, min_value=1)
    runtime = st.selectbox("Runtime", ["runc", "runsc"])
    code = st.text_area("Paste your function code")

    if st.button("Deploy"):
        if name and code:
            data = {
                "name": name,
                "route": route,
                "language": language,
                "timeout": timeout,
                "runtime": runtime,
                "settings": {"code": code}
            }
            res = requests.post(f"{BASE_URL}/functions/", json=data)
            if res.ok:
                st.success("Function deployed successfully!")
            else:
                st.error(f"Failed to deploy: {res.text}")
        else:
            st.warning("Function name and code are required.")

# ---------- Manage Functions ----------
elif page == "Manage Functions":
    st.header("🛠️ Manage Functions")
    res = requests.get(f"{BASE_URL}/functions/")
    if res.ok:
        functions = res.json()
        for idx, func in enumerate(functions):
            func_id = idx + 1
            st.subheader(f"🔹 {func['name']}")
            st.write(f"Language: {func['language']} | Runtime: {func['runtime']} | Timeout: {func['timeout']}s")
            if st.button(f"Delete {func['name']}", key=func['name']):
                delete_res = requests.delete(f"{BASE_URL}/functions/{func_id}")
                if delete_res.ok:
                    st.success(f"{func['name']} deleted.")
                    st.experimental_rerun()
                else:
                    st.error(f"Failed to delete {func['name']}")
    else:
        st.error("Error fetching functions.")

# ---------- Run Function ----------
elif page == "Run Function":
    st.header("🚀 Execute Function")
    res = requests.get(f"{BASE_URL}/functions/")
    if res.ok:
        funcs = res.json()
        if funcs:
            selection = st.selectbox("Select Function", list(enumerate(funcs)), format_func=lambda x: x[1]['name'])
            func_id = selection[0] + 1
            if st.button("Run"):
                run_res = requests.post(f"{BASE_URL}/functions/{func_id}/run")
                if run_res.ok:
                    st.success("Executed Successfully!")
                    st.write(run_res.json())
                else:
                    st.error(f"Execution failed: {run_res.text}")
    else:
        st.error("Couldn't fetch functions.")

# ---------- Function Metrics ----------
elif page == "Function Metrics":
    st.header("📈 View Function Metrics")
    res = requests.get(f"{BASE_URL}/functions/")
    if res.ok:
        funcs = res.json()
        if funcs:
            selection = st.selectbox("Choose Function", list(enumerate(funcs)), format_func=lambda x: x[1]['name'])
            func_id = selection[0] + 1
            metric_res = requests.get(f"{BASE_URL}/functions/{func_id}/metrics")
            if metric_res.ok:
                metrics = metric_res.json()["metrics"]
                st.json(metrics)
            else:
                st.warning("No metrics available for this function.")
    else:
        st.error("Couldn't load functions.")

# ---------- Monitoring Dashboard ----------
elif page == "Monitoring Dashboard":
    st.header("📊 Aggregated Metrics")
    res = requests.get(f"{BASE_URL}/metrics/")
    if res.ok:
        all_metrics = res.json()["metrics"]
        for m in all_metrics:
            st.subheader(f"🔧 {m['function_name']} ({m['runtime']})")
            st.write(f"⏱ Avg Time: {m['avg_response_time']} ms")
            st.write(f"🔥 CPU: {m['avg_cpu_usage_percent']}%")
            st.write(f"🧠 Memory: {m['avg_memory_usage_mb']} MB")
            st.write(f"❌ Errors: {m['error_count']}")
            st.markdown("---")
    else:
        st.error("Error fetching monitoring data.")

