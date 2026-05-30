import streamlit as st
from database import get_session, validar_token, verificar_senha, hash_senha, Condominio, Usuario

def show_login():
    st.markdown("### Acessar Conta")
    
    telefone = st.text_input("Telefone", key="login_telefone", placeholder="Digite seu telefone (apenas números)")
    senha = st.text_input("Senha", type="password", key="login_senha", placeholder="Digite sua senha")
    
    if st.button("Entrar", type="primary", use_container_width=True):
        if not telefone or not senha:
            st.warning("⚠️ Por favor, preencha todos os campos.")
            return
        
        session = get_session()
        try:
            usuario = session.query(Usuario).filter(
                Usuario.telefone == telefone
            ).first()

            if usuario and verificar_senha(senha, usuario.senha_hash):
                st.session_state["authenticated"] = True
                st.session_state["usuario_id"] = usuario.id
                st.session_state["nome"] = usuario.nome
                st.session_state["is_vendedor"] = usuario.is_vendedor
                st.session_state["condominio_id"] = usuario.condominio_id
                st.toast(f"🔑 Bem-vindo de volta, {usuario.nome}!")
                st.rerun()
            else:
                st.error("❌ Credenciais inválidas.")
        except Exception as e:
            st.error(f"❌ Erro ao autenticar: {e}")
        finally:
            session.close()

def show_cadastro():
    st.markdown("### Criar uma Conta")
    
    if "cadastro_token_valido" not in st.session_state:
        st.session_state["cadastro_token_valido"] = False
    if "cadastro_token" not in st.session_state:
        st.session_state["cadastro_token"] = ""
        
    if not st.session_state["cadastro_token_valido"]:
        token_condominio = st.text_input(
            "Código do Condomínio (Token)", 
            key="token_condominio_input", 
            placeholder="Digite o token de acesso do condomínio"
        )
        if st.button("Validar Token", type="primary", use_container_width=True):
            if not token_condominio:
                st.warning("⚠️ Por favor, informe o token.")
            elif validar_token(token_condominio):
                st.session_state["cadastro_token_valido"] = True
                st.session_state["cadastro_token"] = token_condominio
                st.toast("✅ Token validado com sucesso!")
                st.rerun()
            else:
                st.error("❌ Token de condomínio inválido.")
    else:
        st.info("ℹ️ Cadastro para o condomínio validado.")
        
        # Colocando os campos em expanders para reduzir o tamanho vertical da tela
        with st.expander("👤 Informações Pessoais", expanded=True):
            nome = st.text_input("Nome completo", key="cad_nome", placeholder="Digite seu nome completo")
            telefone = st.text_input("Telefone", key="cad_telefone", placeholder="Ex: 11999999999")
            senha = st.text_input("Senha", type="password", key="cad_senha", placeholder="Escolha uma senha segura")
            is_vendedor = st.checkbox("Sou prestador de serviço (Quero vender no marketplace)", key="cad_is_vendedor")
            
        with st.expander("🏢 Endereço e Localização", expanded=False):
            st.caption("Seus dados de localização são sigilosos e nunca serão exibidos publicamente.")
            col_bloco, col_ap = st.columns(2)
            with col_bloco:
                bloco = st.text_input("Bloco", key="cad_bloco", placeholder="Bloco (ex: A)")
            with col_ap:
                apartamento = st.text_input("Apartamento", key="cad_apartamento", placeholder="Apto (ex: 101)")
            
        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            if st.button("Voltar", key="cad_btn_voltar", use_container_width=True):
                st.session_state["cadastro_token_valido"] = False
                st.session_state["cadastro_token"] = ""
                st.rerun()
        with col_btn2:
            cadastrar = st.button("Cadastrar", key="cad_btn_confirmar", type="primary", use_container_width=True)
            
        if cadastrar:
            if not nome or not telefone or not senha or not bloco or not apartamento:
                st.error("⚠️ Por favor, preencha todos os campos do formulário.")
                return
            
            session = get_session()
            try:
                usuario_existente = session.query(Usuario).filter(Usuario.telefone == telefone).first()
                if usuario_existente:
                    st.error("⚠️ Este telefone já está cadastrado.")
                    return
                
                token_condominio = st.session_state["cadastro_token"]
                condominio = session.query(Condominio).filter(Condominio.token_acesso == token_condominio.strip().upper()).first()
                if not condominio:
                    st.error("❌ Condomínio não encontrado.")
                    return

                senha_hash = hash_senha(senha)
                
                novo_usuario = Usuario(
                    nome=nome,
                    bloco=bloco,
                    apartamento=apartamento,
                    telefone=telefone,
                    senha_hash=senha_hash,
                    condominio_id=condominio.id,
                    is_vendedor=is_vendedor
                )
                
                session.add(novo_usuario)
                session.commit()
                
                st.toast("🎉 Cadastro realizado com sucesso!")
                st.success("✅ Cadastro realizado com sucesso! Acesse a aba 'Login' para entrar na sua conta.")
                
                st.session_state["cadastro_token_valido"] = False
                st.session_state["cadastro_token"] = ""
            except Exception as e:
                session.rollback()
                st.error(f"❌ Erro ao salvar cadastro: {e}")
            finally:
                session.close()

def show_auth_page():
    # Cabeçalho centralizado com emoji
    st.markdown(
        """
        <div style='text-align: center; margin-bottom: 2rem;'>
            <h1 style='font-size: 2.2rem; margin-bottom: 0.5rem;'>👋 Olá! Bem-vindo</h1>
            <p style='color: #666; font-size: 1.1rem;'>Acesse ou crie sua conta no Marketplace do seu Condomínio</p>
        </div>
        """,
        unsafe_allow_html=True
    )
    
    tab_login, tab_cadastro = st.tabs(["🔑 Login", "📝 Cadastro"])
    with tab_login:
        show_login()
    with tab_cadastro:
        show_cadastro()

def logout():
    st.session_state.clear()
