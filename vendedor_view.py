import os
import streamlit as st
from database import get_session, PerfilComercial

def show_painel_vendedor():
    usuario_id = st.session_state.get("usuario_id")
    if not usuario_id:
        st.error("Usuario nao esta logado.")
        return

    session = get_session()
    try:
        perfil = session.query(PerfilComercial).filter(
            PerfilComercial.usuario_id == usuario_id
        ).first()
    except Exception as e:
        session.close()
        st.error(f"Erro ao carregar perfil: {e}")
        return

    if "edit_mode" not in st.session_state:
        st.session_state.edit_mode = False

    categorias = ["Alimentacao", "Servicos Gerais", "Beleza", "Pet", "Aulas/Educacao", "Outros"]

    if not perfil:
        st.subheader("Cadastrar Perfil do Prestador")
        with st.form("cadastro_perfil_form"):
            nome_negocio = st.text_input("Nome do Negocio")
            categoria = st.selectbox("Categoria", options=categorias)
            descricao = st.text_area("Descricao (max. 300 caracteres)", max_chars=300)
            whatsapp = st.text_input("WhatsApp", placeholder="Somente numeros, ex: 11999990000",
                                     help="Somente numeros, ex: 11999990000")
            pdf_file = st.file_uploader("Cardapio ou Catalogo (opcional)", type=["pdf"])
            submit = st.form_submit_button("Criar Perfil")

            if submit:
                if not nome_negocio.strip():
                    st.error("Nome do Negocio e obrigatorio.")
                    session.close()
                    return
                if not whatsapp.strip():
                    st.error("WhatsApp e obrigatorio.")
                    session.close()
                    return

                pdf_path = None
                if pdf_file is not None:
                    os.makedirs("./uploads", exist_ok=True)
                    pdf_path = f"./uploads/{usuario_id}_cardapio.pdf"
                    with open(pdf_path, "wb") as f:
                        f.write(pdf_file.getbuffer())

                novo_perfil = PerfilComercial(
                    usuario_id=usuario_id, nome_negocio=nome_negocio.strip(),
                    categoria=categoria, descricao=descricao.strip(),
                    whatsapp=whatsapp.strip(), pdf_cardapio=pdf_path
                )
                try:
                    session.add(novo_perfil)
                    session.commit()
                    st.success("Perfil criado com sucesso!")
                    st.rerun()
                except Exception as e:
                    session.rollback()
                    st.error(f"Erro ao salvar perfil: {e}")
                finally:
                    session.close()
        session.close()

    elif st.session_state.edit_mode:
        st.subheader("Editar Perfil do Prestador")
        try:
            default_index = categorias.index(perfil.categoria)
        except ValueError:
            default_index = 0

        with st.form("edicao_perfil_form"):
            nome_negocio = st.text_input("Nome do Negocio", value=perfil.nome_negocio)
            categoria = st.selectbox("Categoria", options=categorias, index=default_index)
            descricao = st.text_area("Descricao (max. 300 caracteres)", value=perfil.descricao, max_chars=300)
            whatsapp = st.text_input("WhatsApp", value=perfil.whatsapp,
                                     placeholder="Somente numeros, ex: 11999990000")
            if perfil.pdf_cardapio:
                st.info(f"Cardapio atual: {os.path.basename(perfil.pdf_cardapio)}")
            pdf_file = st.file_uploader("Cardapio ou Catalogo (opcional)", type=["pdf"])
            submit = st.form_submit_button("Salvar Alteracoes")

            if submit:
                if not nome_negocio.strip():
                    st.error("Nome do Negocio e obrigatorio.")
                    session.close()
                    return
                if not whatsapp.strip():
                    st.error("WhatsApp e obrigatorio.")
                    session.close()
                    return

                pdf_path = perfil.pdf_cardapio
                if pdf_file is not None:
                    os.makedirs("./uploads", exist_ok=True)
                    pdf_path = f"./uploads/{usuario_id}_cardapio.pdf"
                    with open(pdf_path, "wb") as f:
                        f.write(pdf_file.getbuffer())

                perfil.nome_negocio = nome_negocio.strip()
                perfil.categoria = categoria
                perfil.descricao = descricao.strip()
                perfil.whatsapp = whatsapp.strip()
                perfil.pdf_cardapio = pdf_path
                try:
                    session.commit()
                    st.success("Perfil atualizado com sucesso!")
                    st.session_state.edit_mode = False
                    st.rerun()
                except Exception as e:
                    session.rollback()
                    st.error(f"Erro ao atualizar perfil: {e}")
                finally:
                    session.close()

        if st.button("Cancelar"):
            st.session_state.edit_mode = False
            session.close()
            st.rerun()

    else:
        st.subheader(perfil.nome_negocio)
        st.write(f"**Categoria:** {perfil.categoria}")
        st.write(f"**WhatsApp:** {perfil.whatsapp}")
        st.write("**Descricao:**")
        st.write(perfil.descricao)
        if perfil.pdf_cardapio and os.path.exists(perfil.pdf_cardapio):
            with open(perfil.pdf_cardapio, "rb") as f:
                st.download_button(label="Baixar Cardapio / Catalogo", data=f,
                                   file_name=os.path.basename(perfil.pdf_cardapio),
                                   mime="application/pdf")
        if st.button("Editar Perfil"):
            st.session_state.edit_mode = True
            session.close()
            st.rerun()
        session.close()


def show_vendedor_page():
    os.makedirs("./uploads", exist_ok=True)
    st.title("Painel do Prestador")
    st.info("Seu perfil e visivel apenas para moradores do seu condominio.")
    show_painel_vendedor()
