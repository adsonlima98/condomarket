import streamlit as st
from database import buscar_vendedores_por_condominio

def emoji_por_segmento(segmento: str) -> str:
    if not segmento:
        return "🛍️"
    s = segmento.lower()
    if any(p in s for p in ["bolo", "doce", "alimentação", "sobremesa", "confeitaria"]):
        return "🍰"
    if any(p in s for p in ["hambúrguer", "hamburger", "lanche", "burger"]):
        return "🍔"
    if any(p in s for p in ["pizza", "pizzaria"]):
        return "🍕"
    if any(p in s for p in ["marmita", "almoço", "almoco", "comida", "refeição"]):
        return "🍱"
    if any(p in s for p in ["elétrica", "eletrica", "tomada", "fiação", "instalação"]):
        return "⚡"
    if any(p in s for p in ["beleza", "cabelo", "manicure", "estética", "estetica", "sobrancelha"]):
        return "💅"
    if any(p in s for p in ["pet", "animal", "cachorro", "gato", "banho e tosa"]):
        return "🐾"
    if any(p in s for p in ["aula", "educação", "educacao", "reforço", "escola", "inglês"]):
        return "📚"
    if any(p in s for p in ["limpeza", "faxina", "diarista"]):
        return "🧹"
    if any(p in s for p in ["serviço", "servico", "reparo", "conserto", "geral", "manutenção"]):
        return "🛠️"
    if any(p in s for p in ["alimentação"]):
        return "🍰"
    return "🛍️"

def obter_tipo_anuncio(categoria: str) -> str:
    if not categoria:
        return "PRODUTO"
    cat_lower = categoria.lower()
    palavras_servico = ["serviço", "servico", "aula", "educa", "beleza", "estética", "estetica", "manuten", "consert", "repar", "limpeza", "diarista", "pet"]
    if any(p in cat_lower for p in palavras_servico):
        return "SERVIÇO"
    return "PRODUTO"

def _renderizar_card(perfil):
    with st.container(border=True):
        col1, col2, col3 = st.columns([1, 4.5, 2.5])
        
        with col1:
            emoji = emoji_por_segmento(perfil.categoria or "")
            avatar_html = f"""
            <div style="
                display: flex;
                align-items: center;
                justify-content: center;
                width: 60px;
                height: 60px;
                background-color: #F8F9FA;
                border: 1px solid #E9ECEF;
                border-radius: 12px;
                font-size: 30px;
                margin-top: 5px;
            ">
                {emoji}
            </div>
            """
            st.markdown(avatar_html, unsafe_allow_html=True)
            
        with col2:
            tipo = obter_tipo_anuncio(perfil.categoria or "")
            if tipo == "PRODUTO":
                tag_html = '<span style="background-color: #FFF0E6; color: #FF6B00; border: 1px solid #FFE0B2; padding: 3px 10px; border-radius: 12px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; display: inline-block;">PRODUTO</span>'
            else:
                tag_html = '<span style="background-color: #E3F2FD; color: #1565C0; border: 1px solid #BBDEFB; padding: 3px 10px; border-radius: 12px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; display: inline-block;">SERVIÇO</span>'
            
            st.markdown(tag_html, unsafe_allow_html=True)
            st.markdown(f"<h3 style='margin: 0 0 4px 0; font-size: 1.25rem; font-weight: 600;'>{perfil.nome_negocio}</h3>", unsafe_allow_html=True)
            st.markdown(f"<p style='color: #6C757D; font-size: 0.9rem; margin: 0 0 8px 0; font-weight: 500;'>🏷️ {perfil.categoria or 'Sem categoria'}</p>", unsafe_allow_html=True)
            st.write(perfil.descricao or "")
            
        with col3:
            if perfil.pdf_cardapio is not None:
                try:
                    with open(perfil.pdf_cardapio, "rb") as f:
                        pdf_bytes = f.read()
                    st.download_button(
                        "📄 Cardápio/Catálogo",
                        data=pdf_bytes,
                        file_name=f"cardapio_{perfil.nome_negocio}.pdf",
                        mime="application/pdf",
                        use_container_width=True,
                        key=f"pdf_{perfil.id}"
                    )
                except FileNotFoundError:
                    st.caption("📄 PDF indisponível")
                    
            if perfil.whatsapp:
                numero = perfil.whatsapp.strip().replace(" ","").replace("-","").replace("(","").replace(")","")
                url = f"https://wa.me/55{numero}?text=Ol%C3%A1%2C+vim+pelo+Marketplace+do+condom%C3%ADnio%21"
                whatsapp_button_html = f"""
                <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
                <a class="whatsapp-custom-btn" href="{url}" target="_blank">
                    <i class="fa-brands fa-whatsapp" style="margin-right: 8px;"></i> Chamar no WhatsApp
                </a>
                """
                st.markdown(whatsapp_button_html, unsafe_allow_html=True)
            else:
                st.caption("Sem contato")

def show_feed():
    vendedores = buscar_vendedores_por_condominio(st.session_state["condominio_id"])
    if not vendedores:
        st.info("😕 Nenhum prestador cadastrado no seu condomínio ainda.")
        return

    col1, col2 = st.columns([2, 1])
    with col1:
        keyword = st.text_input("🔍 Buscar por nome ou descrição", placeholder="Ex: bolo, elétrica...")
    with col2:
        categorias_unicas = ["Todas"] + sorted({v.categoria for v in vendedores if v.categoria})
        categoria_sel = st.selectbox("🏷️ Filtrar por categoria", categorias_unicas)

    resultados = vendedores
    if keyword:
        kw = keyword.lower()
        resultados = [
            v for v in resultados
            if kw in (v.nome_negocio or "").lower() or kw in (v.descricao or "").lower()
        ]
    if categoria_sel != "Todas":
        resultados = [v for v in resultados if v.categoria == categoria_sel]

    st.divider()

    if not resultados:
        st.warning("🔎 Nenhum resultado para os filtros aplicados.")
    else:
        st.caption(f"✅ {len(resultados)} prestador(es) encontrado(s)")
        for perfil in resultados:
            _renderizar_card(perfil)

def show_cliente_page():
    st.title("🏪 Marketplace do Condomínio")
    st.caption(f"Olá, **{st.session_state.get('nome','Morador')}**! Veja os prestadores do seu condomínio.")
    st.divider()
    # Injetar CSS global para os botões customizados do WhatsApp
    st.markdown(
        """
        <style>
        .whatsapp-custom-btn {
            display: inline-flex !important;
            align-items: center !important;
            justify-content: center !important;
            background-color: #25D366 !important;
            color: white !important;
            padding: 8px 16px !important;
            border-radius: 8px !important;
            text-decoration: none !important;
            font-weight: bold !important;
            font-size: 14px !important;
            border: none !important;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1) !important;
            transition: background-color 0.2s ease-in-out, transform 0.2s !important;
            width: 100% !important;
            box-sizing: border-box !important;
            text-align: center !important;
            min-height: 38px !important;
            cursor: pointer !important;
        }
        .whatsapp-custom-btn:hover {
            background-color: #128C7E !important;
            transform: translateY(-1px) !important;
            box-shadow: 0 4px 6px rgba(0,0,0,0.15) !important;
            color: white !important;
            text-decoration: none !important;
        }
        .whatsapp-custom-btn i {
            margin-right: 8px !important;
            font-size: 16px !important;
        }
        </style>
        """,
        unsafe_allow_html=True
    )
    show_feed()
