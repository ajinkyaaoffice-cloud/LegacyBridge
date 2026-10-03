       IDENTIFICATION DIVISION.
       PROGRAM-ID. BALUPD.
      *================================================================*
      * BALUPD - DAILY BALANCE UPDATE                                  *
      * INPUT (STDIN):  'A' ACCOUNT RECORDS AND 'T' TRANSACTIONS.      *
      * ACCOUNTS ARE VALIDATED (ACCTVAL) AND LOADED INTO A TABLE OF 20.*
      * TRANSACTIONS ARE POSTED IN INPUT ORDER. AT END OF FILE MONTHLY *
      * FEES (FEECALC) ARE CHARGED AND EVERY ACCOUNT IS PRINTED.       *
      * RETURN CODE 4 WHEN ANY RECORD WAS REJECTED.                    *
      *================================================================*
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT IN-FILE ASSIGN TO KEYBOARD
               ORGANIZATION IS LINE SEQUENTIAL.
       DATA DIVISION.
       FILE SECTION.
       FD  IN-FILE.
       01  IN-ACCT.
           05 IA-REC-TYPE          PIC X.
              88 IA-IS-ACCT        VALUE 'A'.
           05 IA-ACCT-ID           PIC X(8).
           05 IA-NAME              PIC X(20).
           05 IA-TYPE              PIC X.
              88 IA-TYPE-VALUES    VALUE 'C' 'S' 'L'.
           05 IA-BALANCE           PIC S9(9)V99
                                   SIGN LEADING SEPARATE.
           05 IA-RATE              PIC 9V9(4).
           05 IA-OPEN-DATE         PIC 9(8).
           05 IA-STATUS            PIC X.
              88 IA-STATUS-VALUES  VALUE 'A' 'F' 'C'.
       01  IN-TXN.
           05 IT-REC-TYPE          PIC X.
              88 IT-IS-TXN         VALUE 'T'.
           05 IT-ACCT-ID           PIC X(8).
           05 IT-CODE              PIC X.
              88 IT-CODE-VALUES    VALUE 'D' 'W' 'F' 'I'.
           05 IT-AMOUNT            PIC S9(7)V99
                                   SIGN LEADING SEPARATE.
           05 IT-DATE              PIC 9(8).
       WORKING-STORAGE SECTION.
       COPY ACCTREC.
       COPY VALPARM.
       COPY FEEPARM.
       01  WS-EOF                  PIC X VALUE 'N'.
       01  WS-REC-NO               PIC 9(5) VALUE 0.
       01  WS-REJECTS              PIC 9(5) VALUE 0.
       01  WS-OVERFLOW             PIC X VALUE 'N'.
       01  WS-TOT-DEP              PIC S9(8)V99 COMP-3 VALUE 0.
       01  WS-TOT-WD               PIC S9(8)V99 COMP-3 VALUE 0.
       01  WS-TOT-FEES             PIC S9(8)V99 COMP-3 VALUE 0.
       01  WS-NEW-BAL              PIC S9(9)V99 COMP-3.
       01  WS-LIMIT                PIC S9(9)V99 COMP-3.
       01  WS-FOUND                PIC 99.
       01  WS-I                    PIC 99.
       01  WS-REASON               PIC X(30).
       01  WS-TABLE.
           05 WS-COUNT             PIC 99 VALUE 0.
           05 WS-ENTRY OCCURS 20 TIMES.
              10 WT-ID             PIC X(8).
              10 WT-NAME           PIC X(20).
              10 WT-TYPE           PIC X.
              10 WT-BALANCE        PIC S9(9)V99 COMP-3.
              10 WT-TXNS           PIC 9(3) COMP-3.
       01  OUT-ACCT-LINE.
           05 FILLER               PIC X(5) VALUE 'ACCT '.
           05 OA-ID                PIC X(8).
           05 FILLER               PIC X VALUE SPACE.
           05 OA-NAME              PIC X(20).
           05 FILLER               PIC X(5) VALUE ' BAL='.
           05 OA-BAL               PIC -ZZZ,ZZZ,ZZ9.99.
           05 FILLER               PIC X(5) VALUE ' FEE='.
           05 OA-FEE               PIC ZZ9.99.
           05 FILLER               PIC X(6) VALUE ' TXNS='.
           05 OA-TXNS              PIC ZZ9.
       01  OUT-REJECT-LINE.
           05 FILLER               PIC X(7) VALUE 'REJECT '.
           05 OR-REC-NO            PIC 9(5).
           05 FILLER               PIC X VALUE SPACE.
           05 OR-ID                PIC X(8).
           05 FILLER               PIC X VALUE SPACE.
           05 OR-REASON            PIC X(30).
       01  OUT-TOTAL-LINE.
           05 FILLER               PIC X(12) VALUE 'TOTALS: DEP='.
           05 OT-DEP               PIC ZZ,ZZZ,ZZ9.99.
           05 FILLER               PIC X(4) VALUE ' WD='.
           05 OT-WD                PIC ZZ,ZZZ,ZZ9.99.
           05 FILLER               PIC X(6) VALUE ' FEES='.
           05 OT-FEES              PIC ZZ,ZZZ,ZZ9.99.
           05 FILLER               PIC X(9) VALUE ' REJECTS='.
           05 OT-REJ               PIC ZZZZ9.
       PROCEDURE DIVISION.
       0000-MAIN.
           OPEN INPUT IN-FILE
           PERFORM UNTIL WS-EOF = 'Y'
               READ IN-FILE
                   AT END
                       MOVE 'Y' TO WS-EOF
                   NOT AT END
                       ADD 1 TO WS-REC-NO
                       PERFORM 1000-PROCESS-RECORD
               END-READ
           END-PERFORM
           CLOSE IN-FILE
           PERFORM 3000-END-OF-DAY
           IF WS-REJECTS > 0
               MOVE 4 TO RETURN-CODE
           END-IF
           STOP RUN.

       1000-PROCESS-RECORD.
           EVALUATE TRUE
               WHEN IA-IS-ACCT
                   PERFORM 1100-LOAD-ACCOUNT
               WHEN IT-IS-TXN
                   PERFORM 2000-POST-TRANSACTION
               WHEN OTHER
                   MOVE 'UNKNOWN RECORD TYPE' TO WS-REASON
                   PERFORM 9000-REJECT
           END-EVALUATE.

       1100-LOAD-ACCOUNT.
           IF IA-BALANCE NOT NUMERIC OR IA-RATE NOT NUMERIC
              OR IA-OPEN-DATE NOT NUMERIC
               MOVE 'BAD ACCOUNT RECORD' TO WS-REASON
               PERFORM 9000-REJECT
           ELSE
               MOVE IA-ACCT-ID TO ACCT-ID
               MOVE IA-NAME TO ACCT-NAME
               MOVE IA-TYPE TO ACCT-TYPE
               MOVE IA-BALANCE TO ACCT-BALANCE
               MOVE IA-RATE TO ACCT-RATE
               MOVE IA-OPEN-DATE TO ACCT-OPEN-DATE
               MOVE IA-STATUS TO ACCT-STATUS
               MOVE 'V' TO VP-FUNCTION
               CALL 'ACCTVAL' USING ACCT-REC VAL-PARMS
               IF VP-RESULT NOT = 'OK'
                   MOVE SPACES TO WS-REASON
                   STRING VP-RESULT ' ' VP-MESSAGE
                       DELIMITED BY SIZE INTO WS-REASON
                   END-STRING
                   PERFORM 9000-REJECT
               ELSE
                   PERFORM 1200-ADD-TO-TABLE
               END-IF
           END-IF.

       1200-ADD-TO-TABLE.
           PERFORM 2100-FIND-ACCOUNT
           IF WS-FOUND > 0
               MOVE 'DUPLICATE ACCOUNT' TO WS-REASON
               PERFORM 9000-REJECT
           ELSE
               IF WS-COUNT = 20
                   MOVE 'ACCOUNT TABLE FULL' TO WS-REASON
                   PERFORM 9000-REJECT
               ELSE
                   ADD 1 TO WS-COUNT
                   MOVE ACCT-ID TO WT-ID(WS-COUNT)
                   MOVE ACCT-NAME TO WT-NAME(WS-COUNT)
                   MOVE ACCT-TYPE TO WT-TYPE(WS-COUNT)
                   MOVE ACCT-BALANCE TO WT-BALANCE(WS-COUNT)
                   MOVE 0 TO WT-TXNS(WS-COUNT)
               END-IF
           END-IF.

       2000-POST-TRANSACTION.
           MOVE IT-ACCT-ID TO ACCT-ID
           IF IT-AMOUNT NOT NUMERIC OR IT-DATE NOT NUMERIC
               MOVE 'BAD TRANSACTION RECORD' TO WS-REASON
               PERFORM 9000-REJECT
           ELSE
           IF IT-AMOUNT NOT > 0
               MOVE 'NON-POSITIVE AMOUNT' TO WS-REASON
               PERFORM 9000-REJECT
           ELSE
               PERFORM 2100-FIND-ACCOUNT
               IF WS-FOUND = 0
                   MOVE 'ACCOUNT NOT ON FILE' TO WS-REASON
                   PERFORM 9000-REJECT
               ELSE
                   PERFORM 2200-APPLY
               END-IF
           END-IF
           END-IF.

       2100-FIND-ACCOUNT.
           MOVE 0 TO WS-FOUND
           PERFORM VARYING WS-I FROM 1 BY 1
                   UNTIL WS-I > WS-COUNT OR WS-FOUND > 0
               IF WT-ID(WS-I) = ACCT-ID
                   MOVE WS-I TO WS-FOUND
               END-IF
           END-PERFORM.

       2200-APPLY.
           EVALUATE IT-CODE
               WHEN 'D'
                   ADD IT-AMOUNT TO WT-BALANCE(WS-FOUND)
                   ADD IT-AMOUNT TO WS-TOT-DEP
                       ON SIZE ERROR MOVE 'Y' TO WS-OVERFLOW
                   END-ADD
                   ADD 1 TO WT-TXNS(WS-FOUND)
               WHEN 'W'
                   EVALUATE WT-TYPE(WS-FOUND)
                       WHEN 'C'
                           MOVE -500.00 TO WS-LIMIT
                       WHEN 'S'
                           MOVE 0 TO WS-LIMIT
                       WHEN OTHER
                           MOVE 999999999.99 TO WS-LIMIT
                   END-EVALUATE
                   COMPUTE WS-NEW-BAL = WT-BALANCE(WS-FOUND)
                                      - IT-AMOUNT
                   IF WS-NEW-BAL < WS-LIMIT
                       MOVE 'INSUFFICIENT FUNDS' TO WS-REASON
                       PERFORM 9000-REJECT
                   ELSE
                       MOVE WS-NEW-BAL TO WT-BALANCE(WS-FOUND)
                       ADD IT-AMOUNT TO WS-TOT-WD
                           ON SIZE ERROR MOVE 'Y' TO WS-OVERFLOW
                       END-ADD
                       ADD 1 TO WT-TXNS(WS-FOUND)
                   END-IF
               WHEN 'F'
                   SUBTRACT IT-AMOUNT FROM WT-BALANCE(WS-FOUND)
                   ADD 1 TO WT-TXNS(WS-FOUND)
               WHEN 'I'
                   ADD IT-AMOUNT TO WT-BALANCE(WS-FOUND)
                   ADD 1 TO WT-TXNS(WS-FOUND)
               WHEN OTHER
                   MOVE 'UNKNOWN TRANSACTION CODE' TO WS-REASON
                   PERFORM 9000-REJECT
           END-EVALUATE.

       3000-END-OF-DAY.
           PERFORM VARYING WS-I FROM 1 BY 1 UNTIL WS-I > WS-COUNT
               MOVE WT-TYPE(WS-I) TO FP-ACCT-TYPE
               MOVE WT-BALANCE(WS-I) TO FP-BALANCE
               MOVE WT-TXNS(WS-I) TO FP-TXN-COUNT
               CALL 'FEECALC' USING FEE-PARMS
               SUBTRACT FP-FEE FROM WT-BALANCE(WS-I)
               ADD FP-FEE TO WS-TOT-FEES
               MOVE WT-ID(WS-I) TO OA-ID
               MOVE WT-NAME(WS-I) TO OA-NAME
               MOVE WT-BALANCE(WS-I) TO OA-BAL
               MOVE FP-FEE TO OA-FEE
               MOVE WT-TXNS(WS-I) TO OA-TXNS
               DISPLAY OUT-ACCT-LINE
           END-PERFORM
           MOVE WS-TOT-DEP TO OT-DEP
           MOVE WS-TOT-WD TO OT-WD
           MOVE WS-TOT-FEES TO OT-FEES
           MOVE WS-REJECTS TO OT-REJ
           DISPLAY OUT-TOTAL-LINE
           IF WS-OVERFLOW = 'Y'
               DISPLAY 'WARNING: TOTALS OVERFLOWED'
           END-IF.

       9000-REJECT.
           ADD 1 TO WS-REJECTS
           MOVE WS-REC-NO TO OR-REC-NO
           MOVE IA-ACCT-ID TO OR-ID
           MOVE WS-REASON TO OR-REASON
           DISPLAY OUT-REJECT-LINE.
