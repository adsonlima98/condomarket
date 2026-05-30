import streamlit as st
from database import init_db, get_session
from auth import show_auth_page, logout
from admin_view import mostrar_painel_admin
from cliente_view import show_cliente_page
from vendedor_view import show_vendedor_page

st.set_page_config(page_title="Marketplace Condomínio", page_icon="🏪", layout="centered")

if not st.session_state.get("db_initialized"):
    init_db()
    st.session_state["db_initialized"] = True

if not st.session_state.get("authenticated"):
    show_auth_page()
else:
    session = get_session()
    try:
        usuario_id = st.session_state.get("usuario_id")
        from database import Usuario
        usuario = session.query(Usuario).filter(Usuario.id == usuario_id).first()
        if usuario:
            nome = usuario.nome
            is_admin = usuario.is_admin
            is_vendedor = usuario.is_vendedor
        else:
            nome = st.session_state.get("nome", "")
            is_admin = st.session_state.get("is_admin", False)
            is_vendedor = st.session_state.get("is_vendedor", False)

        if is_admin is True:
            with st.sidebar:
                st.write(f"👤 {nome}")
                st.caption("Perfil: Administrador")
                if st.button("Sair"):
                    logout()
                    st.rerun()
            mostrar_painel_admin(session)
        elif is_vendedor is True:
            with st.sidebar:
                st.write(f"👤 {nome}")
                st.caption("Perfil: Prestador")
                if st.button("Sair"):
                    logout()
                    st.rerun()
            show_vendedor_page()
        else:
            with st.sidebar:
                st.write(f"👤 {nome}")
                st.caption("Perfil: Morador")
                if st.button("Sair"):
                    logout()
                    st.rerun()
            show_cliente_page()
    finally:
        session.close()
