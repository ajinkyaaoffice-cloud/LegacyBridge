       IDENTIFICATION DIVISION.
       PROGRAM-ID. FEECALC.
      *================================================================*
      * FEECALC - MONTHLY FEE SCHEDULE                                 *
      *   CHECKING: 12.00 MAINTENANCE UNLESS BALANCE >= 1500.00        *
      *             0.35 PER TRANSACTION OVER 20                       *
      *             OVERDRAWN: 35.00 + 1.5% OF AMOUNT OVERDRAWN        *
      *   SAVINGS:  5.00 IF BALANCE BELOW 300.00                       *
      *             2.50 PER TRANSACTION OVER 6                        *
      *   LOAN:     NO FEES                                            *
      *   TOTAL FEE IS CAPPED AT 250.00                                *
      *================================================================*
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-MONTHLY-FEE          PIC S9(5)V99 COMP-3.
       01  WS-TXN-FEE              PIC S9(5)V99 COMP-3.
       01  WS-OD-FEE               PIC S9(5)V99 COMP-3.
       01  WS-EXCESS               PIC S9(3) COMP-3.
       LINKAGE SECTION.
       COPY FEEPARM.
       PROCEDURE DIVISION USING FEE-PARMS.
       0000-MAIN.
           MOVE 0 TO WS-MONTHLY-FEE WS-TXN-FEE WS-OD-FEE
           EVALUATE FP-ACCT-TYPE
               WHEN 'C'
                   IF FP-BALANCE < 1500.00
                       MOVE 12.00 TO WS-MONTHLY-FEE
                   END-IF
                   COMPUTE WS-EXCESS = FP-TXN-COUNT - 20
                   IF WS-EXCESS > 0
                       COMPUTE WS-TXN-FEE = WS-EXCESS * 0.35
                   END-IF
                   IF FP-BALANCE < 0
                       COMPUTE WS-OD-FEE ROUNDED =
                           35.00 + (0 - FP-BALANCE) * 0.015
                   END-IF
               WHEN 'S'
                   IF FP-BALANCE < 300.00
                       MOVE 5.00 TO WS-MONTHLY-FEE
                   END-IF
                   COMPUTE WS-EXCESS = FP-TXN-COUNT - 6
                   IF WS-EXCESS > 0
                       COMPUTE WS-TXN-FEE = WS-EXCESS * 2.50
                   END-IF
               WHEN 'L'
                   CONTINUE
               WHEN OTHER
                   MOVE 0 TO FP-FEE
                   MOVE 'X' TO FP-WAIVED
                   GOBACK
           END-EVALUATE
           COMPUTE FP-FEE = WS-MONTHLY-FEE + WS-TXN-FEE + WS-OD-FEE
           IF FP-FEE > 250.00
               MOVE 250.00 TO FP-FEE
           END-IF
           IF FP-FEE = 0
               MOVE 'Y' TO FP-WAIVED
           ELSE
               MOVE 'N' TO FP-WAIVED
           END-IF
           GOBACK.
