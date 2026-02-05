from google import genai

client = genai.Client(api_key="AIzaSyCxSuSXj6PaJUECByGqcZQFCZ_7PNVc788")

result = client.models.embed_content(
        model="gemini-embedding-001",
        contents= [
            "What is the meaning of life?",
            "What is the purpose of existence?",
            "How do I bake a cake?"
        ]
)

for embedding in result.embeddings:
    print(embedding)