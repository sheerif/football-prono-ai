import streamlit as st

def stat_card(title, value, delta=None):
    st.metric(title, value, delta)
