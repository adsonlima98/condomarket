import streamlit as st
import pandas as pd
from database import Condominio, Usuario

def mostrar_painel_admin(session):
    # CSS Customizado para deixar o dashboard ainda mais atraente
    st.markdown("""
        <style>
        div[data-testid="stMetric"] {
            background-color: #ffffff;
            border: 1px solid #f0f2f6;
            padding: 15px 20px;
            border-radius: 12px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.02);
            transition: transform 0.2s ease-in-out, box-shadow 0.2s ease-in-out;
        }
        div[data-testid="stMetric"]:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 12px rgba(0, 0, 0, 0.05);
            border-color: #e2e8f0;
        }
        </style>
    """, unsafe_allow_html=True)

    st.title("🏢 Painel de Controle do Administrador")
    st.caption("Acompanhe o crescimento da plataforma e gerencie os condomínios cadastrados.")

    # 1) Organizar os blocos de métricas (st.metric) em colunas paralelas no topo da página
    total_condominios = session.query(Condominio).count()
    total_usuarios = session.query(Usuario).count()
    total_moradores = session.query(Usuario).filter(Usuario.is_admin == False, Usuario.is_vendedor == False).count()
    total_prestadores = session.query(Usuario).filter(Usuario.is_vendedor == True).count()

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric(label="🏢 Condomínios", value=total_condominios)
    with col2:
        st.metric(label="👥 Total Usuários", value=total_usuarios)
    with col3:
        st.metric(label="🏠 Moradores", value=total_moradores)
    with col4:
        st.metric(label="🏪 Prestadores", value=total_prestadores)

    # 2) Adicionar uma linha divisória estética (st.divider())
    st.divider()

    # Divisão de layout: Tabela e Formulário
    col_tabela, col_form = st.columns([1.6, 1.0], gap="large")

    with col_tabela:
        st.subheader("📋 Condomínios Cadastrados")
        
        condominios = session.query(Condominio).all()
        if not condominios:
            st.info("Nenhum condomínio cadastrado ainda.")
        else:
            dados = [{"ID": c.id, "Nome": c.nome, "Token": c.token_acesso} for c in condominios]
            df = pd.DataFrame(dados)

            # 3) Estilizar a tabela usando recursos avançados do st.dataframe, permitindo ordenação e busca rápida
            # Adiciona busca rápida adicional antes da tabela
            busca = st.text_input("🔍 Busca rápida", placeholder="Digite nome ou token para filtrar...")
            if busca:
                df = df[
                    df["Nome"].str.contains(busca, case=False, na=False) | 
                    df["Token"].str.contains(busca, case=False, na=False)
                ]

            st.dataframe(
                df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "ID": st.column_config.NumberColumn(
                        "🔢 ID", 
                        help="ID sequencial do condomínio no banco de dados", 
                        width="small"
                    ),
                    "Nome": st.column_config.TextColumn(
                        "🏢 Nome do Condomínio", 
                        help="Nome comercial/residencial cadastrado", 
                        width="large"
                    ),
                    "Token": st.column_config.TextColumn(
                        "🔑 Token de Acesso", 
                        help="Código utilizado pelos moradores no momento do cadastro", 
                        width="medium"
                    )
                }
            )

    with col_form:
        # 4) Colocar o formulário dentro de um card com borda (st.container com border=True)
        st.subheader("➕ Novo Cadastro")
        with st.container(border=True):
            st.markdown("##### Registrar Condomínio")
            st.caption("Crie um novo condomínio e defina um token de acesso para os moradores.")
            
            with st.form("form_novo_condominio", clear_on_submit=True):
                nome = st.text_input("🏠 Nome do Condomínio", placeholder="Ex: Residencial Aurora")
                token = st.text_input("🔑 Token de Acesso", placeholder="Ex: AURORA2026",
                                    help="Código que os moradores usarão para se cadastrar.")
                submit = st.form_submit_button("✅ Cadastrar", use_container_width=True)

                if submit:
                    if not nome.strip() or not token.strip():
                        st.error("⚠️ Preencha todos os campos.")
                    else:
                        token_upper = token.strip().upper()
                        duplicate = session.query(Condominio).filter_by(token_acesso=token_upper).first()
                        if duplicate:
                            st.error(f"❌ Token '{token_upper}' já está em uso.")
                        else:
                            novo = Condominio(nome=nome.strip(), token_acesso=token_upper)
                            session.add(novo)
                            session.commit()
                            st.success(f"✅ '{nome}' cadastrado!")
                            st.rerun()

    # 5) Seção de Gerenciamento de Usuários
    st.divider()
    st.subheader("👥 Gerenciamento de Usuários")
    st.caption("Consulte e audite as informações de todos os usuários cadastrados, incluindo dados residenciais confidenciais.")

    from sqlalchemy.orm import undefer
    usuarios = (
        session.query(Usuario)
        .options(undefer(Usuario.bloco), undefer(Usuario.apartamento))
        .all()
    )

    if not usuarios:
        st.info("Nenhum usuário cadastrado no sistema.")
    else:
        dados_usuarios = []
        for u in usuarios:
            dados_usuarios.append({
                "Nome": u.nome,
                "Telefone": u.telefone,
                "Bloco": u.bloco,
                "Apartamento": u.apartamento,
                "Condomínio Vinculado": u.condominio.nome if u.condominio else "Nenhum",
                "Vendedor": "Sim" if u.is_vendedor else "Não"
            })

        df_usuarios = pd.DataFrame(dados_usuarios)

        # Filtros de busca ativados
        col_busca_nome, col_busca_bloco = st.columns(2)
        with col_busca_nome:
            busca_nome = st.text_input("🔍 Filtrar por Nome", placeholder="Digite um nome para buscar...", key="busca_usuario_nome")
        with col_busca_bloco:
            busca_bloco = st.text_input("🏢 Filtrar por Bloco", placeholder="Digite o bloco para buscar...", key="busca_usuario_bloco")

        df_filtrado = df_usuarios.copy()
        if busca_nome:
            df_filtrado = df_filtrado[df_filtrado["Nome"].str.contains(busca_nome, case=False, na=False)]
        if busca_bloco:
            df_filtrado = df_filtrado[df_filtrado["Bloco"].str.contains(busca_bloco, case=False, na=False)]

        st.dataframe(
            df_filtrado,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Nome": st.column_config.TextColumn(
                    "👤 Nome",
                    help="Nome completo do usuário",
                    width="medium"
                ),
                "Telefone": st.column_config.TextColumn(
                    "📞 Telefone",
                    help="Telefone de contato",
                    width="medium"
                ),
                "Bloco": st.column_config.TextColumn(
                    "🏢 Bloco",
                    help="Bloco residencial do usuário",
                    width="small"
                ),
                "Apartamento": st.column_config.TextColumn(
                    "🚪 Apartamento",
                    help="Número do apartamento do usuário",
                    width="small"
                ),
                "Condomínio Vinculado": st.column_config.TextColumn(
                    "🏠 Condomínio Vinculado",
                    help="Condomínio ao qual o usuário está associado",
                    width="medium"
                ),
                "Vendedor": st.column_config.TextColumn(
                    "🏪 Vendedor?",
                    help="Indica se o usuário é um prestador de serviços/vendedor",
                    width="small"
                )
            }
        )


